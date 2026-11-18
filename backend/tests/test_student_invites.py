"""一次性注册的权限、并发消费与班级边界。"""

import asyncio
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.security import verify_password
from app.domain.models import AdminAudit, SchoolClass, StudentInvite, User
from app.services.student_invites import digest, issue_invite, redeem_invite

REGISTER = "/api/v1/auth/register"
PASSWORD = "  local-test-pass  "


def registration(code: str, username=" invited.student "):
    return {"username": username, "display_name": " 测试同学 ", "password": PASSWORD, "invite_code": code}


async def test_admin_invite_registers_only_a_student_in_bound_class(api_context):
    ctx = api_context
    group = SchoolClass(name="注册验收班")
    ctx.session.add(group)
    await ctx.session.flush()
    url = f"/api/v1/admin/classes/{group.id}/student-invites"
    admin = ctx.headers(ctx.users.admin)
    for actor in (ctx.users.student, ctx.users.teacher):
        assert (await ctx.client.post(url, headers=ctx.headers(actor))).status_code == 403
    issued = await ctx.client.post(url, headers=admin)
    assert issued.status_code == 201 and issued.headers["cache-control"] == "no-store"
    receipt = issued.json()
    code = receipt.pop("code")
    assert len(code) == 32 and receipt["state"] == "active"
    saved = await ctx.session.get(StudentInvite, receipt["id"])
    assert saved.code_digest == digest(code) and saved.code_digest != code
    listing = await ctx.client.get(url, headers=admin)
    assert listing.json() == {"archived": False, "items": [receipt]}
    assert code not in listing.text

    for extra in ({"role": "admin"}, {"class_id": group.id}, {"password": "short"}):
        denied = await ctx.client.post(REGISTER, json={**registration(code), **extra})
        assert denied.status_code == 422 and code not in denied.text
    duplicate = await ctx.client.post(REGISTER, json=registration(code, ctx.users.student.username.upper()))
    assert duplicate.status_code == 409 and saved.used_by_id is None

    response = await ctx.client.post(REGISTER, json=registration(code))
    assert response.status_code == 201 and response.headers["cache-control"] == "no-store"
    result = response.json()
    user = result["user"]
    assert user == {"id": saved.used_by_id, "role": "student", "username": "invited.student", "display_name": "测试同学",
                    "class_id": group.id, "class_name": group.name, "must_change_password": False}
    account = await ctx.session.get(User, user["id"])
    assert account.password_hash != PASSWORD and verify_password(PASSWORD, account.password_hash)
    identity = await ctx.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {result['access_token']}"})
    assert identity.status_code == 200 and identity.json() == user
    assert (await ctx.client.post(REGISTER, json=registration(code, "next.student"))).status_code == 409
    assert (await ctx.client.get(url, headers=admin)).json()["items"][0]["state"] == "used"
    events = (await ctx.session.scalars(select(AdminAudit).where(
        AdminAudit.action.in_(["class.invite.issue", "student.register"]),
    ))).all()
    assert {row.action for row in events} == {"class.invite.issue", "student.register"}
    assert all(code not in str(row.after) and PASSWORD not in str(row.after) for row in events)


async def test_revoked_expired_and_archived_invites_do_not_register(api_context):
    ctx = api_context
    first, second = SchoolClass(name="即将归档"), SchoolClass(name="另一班")
    ctx.session.add_all([first, second])
    await ctx.session.flush()
    url = f"/api/v1/admin/classes/{first.id}/student-invites"
    admin = ctx.headers(ctx.users.admin)
    codes = []
    for _ in range(3):
        result = await ctx.client.post(url, headers=admin)
        assert result.status_code == 201, result.text
        codes.append(result.json())
    revoked, expired, archived = codes
    wrong_path = f"/api/v1/admin/classes/{second.id}/student-invites/{revoked['id']}/revoke"
    assert (await ctx.client.post(wrong_path, headers=admin)).status_code == 404
    path = f"{url}/{revoked['id']}/revoke"
    first_revoke = await ctx.client.post(path, headers=admin)
    assert first_revoke.status_code == 200 and first_revoke.json()["state"] == "revoked"
    assert (await ctx.client.post(path, headers=admin)).json() == first_revoke.json()
    assert (await ctx.client.post(REGISTER, json=registration(revoked["code"]))).status_code == 409

    dead = await ctx.session.get(StudentInvite, expired["id"])
    dead.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await ctx.session.flush()
    assert (await ctx.client.post(REGISTER, json=registration(expired["code"]))).status_code == 409
    first.archived = True
    await ctx.session.flush()
    assert (await ctx.client.post(REGISTER, json=registration(archived["code"]))).status_code == 409
    assert (await ctx.client.post(url, headers=admin)).status_code == 404
    assert (await ctx.client.post(REGISTER, json=registration("x" * 32))).status_code == 409


async def test_concurrent_registrations_only_consume_a_code_once(isolated_database):
    sessions = async_sessionmaker(isolated_database, expire_on_commit=False)
    async with sessions() as session:
        group = SchoolClass(name="并发验收班")
        actor = User(username="issuer", role="admin")
        session.add_all([group, actor])
        await session.flush()
        issued = await issue_invite(session, actor, group.id)

    async def redeem(username):
        async with sessions() as session:
            try:
                student = await redeem_invite(session, code=issued["code"], username=username,
                                              display_name="验收学生", password=PASSWORD)
                return student.id
            except HTTPException as failure:
                assert failure.status_code == 409
                return None

    results = await asyncio.wait_for(asyncio.gather(redeem("first"), redeem("second")), timeout=20)
    assert results.count(None) == 1
    async with sessions() as session:
        invite = await session.get(StudentInvite, issued["id"])
        assert invite.used_by_id in results
        assert await session.scalar(select(func.count()).select_from(User).where(User.role == "student")) == 1
