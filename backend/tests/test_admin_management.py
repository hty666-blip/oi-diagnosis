"""管理基础回归：真实 MySQL 归属、登录撤销、原子导入及审计。"""
import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.v1.admin import AccountEdit, edit_account
from app.core.security import create_access_token, hash_password
from app.domain.models import AdminAudit, ClassTeacher, SchoolClass, TrainingMember, User


def headers(row):
    return {"Authorization": "Bearer " + create_access_token(row.id, row.role, row.username, row.token_version)}


async def make_class(ctx, name="管理测试班", teacher=None):
    group = SchoolClass(name=name)
    ctx.session.add(group)
    await ctx.session.flush()
    if teacher:
        ctx.session.add(ClassTeacher(class_id=group.id, teacher_id=teacher.id))
        teacher.class_id, teacher.class_name = None, ""
        await ctx.session.flush()
    return group


async def make_training(ctx, group, teacher, *, student=None):
    student = student or ctx.users.student
    student.class_id, student.class_name = group.id, group.name
    await ctx.session.flush()
    payload = {"title": "管理训练", "class_name": group.name,
               "due_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
               "problems": [{"pid": ctx.problem.pid}]}
    response = await ctx.client.post("/api/v1/trainings", headers=headers(teacher), json=payload)
    assert response.status_code == 200, response.text
    tid = response.json()["id"]
    response = await ctx.client.post(f"/api/v1/trainings/{tid}/publish", headers=headers(teacher))
    assert response.status_code == 200, response.text
    return tid


@pytest.mark.parametrize("path", ["overview", "users", "classes", "teachers", "audit"])
async def test_admin_read_routes_are_guarded(api_context, path):
    ctx = api_context
    for role in (ctx.users.student, ctx.users.teacher):
        assert (await ctx.client.get(f"/api/v1/admin/{path}", headers=headers(role))).status_code == 403
    response = await ctx.client.get(f"/api/v1/admin/{path}", headers=headers(ctx.users.admin))
    assert response.status_code == 200, response.text
    if path == "overview":
        assert response.json()["worker_status"] == "unknown"


async def test_account_create_force_change_and_revoke_tokens(api_context):
    ctx = api_context
    payload = {"username": " NEW.student ", "display_name": "新同学", "password": "initial-pass-123"}
    response = await ctx.client.post("/api/v1/admin/users", headers={**headers(ctx.users.admin), "Content-Type": "application/json"}, content=json.dumps(payload))
    assert response.status_code == 200, response.text
    assert response.json()["must_change_password"]
    assert "password" not in response.json() and "password_hash" not in response.json()
    response = await ctx.client.post("/api/v1/auth/login", json={"username": "new.student", "password": payload["password"]})
    token = {"Authorization": "Bearer " + response.json()["access_token"]}
    assert (await ctx.client.get("/api/v1/submissions/my", headers=token)).status_code in (403, 404, 422)
    assert (await ctx.client.get("/api/v1/trainings", headers=token)).status_code == 403
    response = await ctx.client.post("/api/v1/auth/password", headers=token, json={"current_password": payload["password"], "new_password": "replaced-pass-456"})
    assert response.status_code == 200, response.text
    fresh = {"Authorization": "Bearer " + response.json()["access_token"]}
    assert not response.json()["user"]["must_change_password"]
    assert (await ctx.client.get("/api/v1/auth/me", headers=token)).status_code == 401
    assert (await ctx.client.get("/api/v1/trainings", headers=fresh)).status_code == 200
    rows = (await ctx.session.scalars(select(AdminAudit))).all()
    assert len(rows) == 2
    assert all(payload["password"] not in str(r.after) and "password_hash" not in str(r.after) for r in rows)


async def test_disable_restore_reset_reject_old_credentials(api_context):
    ctx = api_context
    row = ctx.users.student
    row.password_hash = hash_password("original-pass-123")
    await ctx.session.flush()
    old = headers(row)
    admin = headers(ctx.users.admin)
    body = {"expected_version": 0, "display_name": "学生", "role": "student", "enabled": False}
    response = await ctx.client.patch(f"/api/v1/admin/users/{row.id}", headers=admin, json=body)
    assert response.status_code == 200, response.text
    assert (await ctx.client.get("/api/v1/auth/me", headers=old)).status_code == 401
    assert (await ctx.client.post("/api/v1/auth/login", json={"username": row.username, "password": "original-pass-123"})).status_code == 401
    body.update(expected_version=1, enabled=True)
    assert (await ctx.client.patch(f"/api/v1/admin/users/{row.id}", headers=admin, json=body)).status_code == 200
    assert (await ctx.client.get("/api/v1/auth/me", headers=old)).status_code == 401
    current = headers(row)
    response = await ctx.client.post(f"/api/v1/admin/users/{row.id}/password", headers=admin, json={"expected_version": 2, "password": "reset-password-456"})
    assert response.status_code == 200 and response.json()["must_change_password"]
    assert (await ctx.client.get("/api/v1/auth/me", headers=current)).status_code == 401
    assert (await ctx.client.post("/api/v1/auth/login", json={"username": row.username, "password": "original-pass-123"})).status_code == 401


@pytest.mark.parametrize("role,enabled", [("student", True), ("admin", False)])
async def test_last_enabled_admin_protected(api_context, role, enabled):
    ctx = api_context
    response = await ctx.client.patch(f"/api/v1/admin/users/{ctx.users.admin.id}", headers=headers(ctx.users.admin), json={"expected_version": 0, "display_name": "管理", "role": role, "enabled": enabled})
    assert response.status_code == 409
    assert await ctx.session.scalar(select(func.count()).select_from(AdminAudit)) == 0


async def test_import_preview_and_commit_recheck_is_atomic(api_context):
    ctx = api_context
    admin = headers(ctx.users.admin)
    rows = [{"username": f"batch-{i}", "display_name": f"学生{i}", "password": "batch-initial-123"} for i in (1, 2)]
    response = await ctx.client.post("/api/v1/admin/users/import/preview", headers=admin, json={"rows": rows})
    assert response.status_code == 200 and response.json()["errors"] == []
    assert "password" not in str(response.json())
    ctx.session.add(User(username="batch-2", display_name="抢先创建", role="student"))
    await ctx.session.flush()
    response = await ctx.client.post("/api/v1/admin/users/import", headers=admin, json={"rows": rows})
    assert response.status_code == 409
    assert await ctx.session.scalar(select(User.id).where(User.username == "batch-1")) is None
    rows[1]["username"] = "BATCH-1"
    response = await ctx.client.post("/api/v1/admin/users/import/preview", headers=admin, json={"rows": rows})
    assert response.json()["errors"][0]["row"] == 2


async def test_multi_class_teacher_rename_preserves_scope_and_snapshot(api_context):
    ctx = api_context
    teacher = ctx.users.teacher
    first = await make_class(ctx, teacher=teacher)
    second = await make_class(ctx, "第二管理班", teacher=teacher)
    tid = await make_training(ctx, first, teacher)
    await make_training(ctx, second, teacher, student=ctx.users.other)
    assert len((await ctx.client.get("/api/v1/trainings", headers=headers(teacher))).json()) == 2
    response = await ctx.client.patch(f"/api/v1/admin/classes/{first.id}", headers=headers(ctx.users.admin), json={"name": "改名后管理班", "expected_version": 0, "archived": False, "teacher_ids": [teacher.id], "transfer_to": None})
    assert response.status_code == 200, response.text
    response = await ctx.client.get(f"/api/v1/trainings/{tid}", headers=headers(ctx.users.student))
    assert response.status_code == 200 and response.json()["class_name"] == "改名后管理班"
    assert len((await ctx.client.get("/api/v1/trainings/roster", headers=headers(teacher))).json()) == 2
    assert (await ctx.client.get(f"/api/v1/trainings/{tid}", headers=headers(ctx.users.other_teacher))).status_code == 404
    assert await ctx.session.get(TrainingMember, (tid, ctx.users.student.id)) is not None


async def test_managed_teacher_submission_scope_does_not_expand_to_legacy_names(api_context):
    from app.services.access import accessible_submission
    ctx = api_context
    teacher = ctx.users.teacher
    group = await make_class(ctx, teacher=teacher)
    ctx.users.student.class_id, ctx.users.student.class_name = group.id, group.name
    # 同名但没有稳定编号的旧记录，不能因为名称相同进入新班授权。
    ctx.users.peer.class_name = group.name
    own = await ctx.submission(ctx.users.student)
    legacy = await ctx.submission(ctx.users.peer)
    assert (await accessible_submission(ctx.session, teacher, own.id)).id == own.id
    with pytest.raises(HTTPException) as error:
        await accessible_submission(ctx.session, teacher, legacy.id)
    assert error.value.status_code == 404


async def test_transfer_retains_membership_without_adding_new_published_training(api_context):
    ctx = api_context
    first = await make_class(ctx, teacher=ctx.users.teacher)
    second = await make_class(ctx, "转入班", teacher=ctx.users.other_teacher)
    tid = await make_training(ctx, first, ctx.users.teacher)
    other_tid = await make_training(ctx, second, ctx.users.other_teacher, student=ctx.users.other)
    old = headers(ctx.users.student)
    payload = {"expected_version": 0, "class_id": second.id}
    response = await ctx.client.post(f"/api/v1/admin/users/{ctx.users.student.id}/transfer/preview", headers=headers(ctx.users.admin), json=payload)
    assert response.status_code == 200 and response.json()["affected_trainings"] == 1
    response = await ctx.client.post(f"/api/v1/admin/users/{ctx.users.student.id}/transfer", headers=headers(ctx.users.admin), json=payload)
    assert response.status_code == 200, response.text
    assert (await ctx.client.get("/api/v1/trainings", headers=old)).status_code == 401
    fresh = headers(ctx.users.student)
    assert (await ctx.client.get(f"/api/v1/trainings/{tid}", headers=fresh)).status_code == 404
    assert (await ctx.client.get(f"/api/v1/trainings/{other_tid}", headers=fresh)).status_code == 404
    assert await ctx.session.get(TrainingMember, (tid, ctx.users.student.id)) is not None
    assert await ctx.session.get(TrainingMember, (other_tid, ctx.users.student.id)) is None


async def test_explicit_training_handoff_preserves_original_teacher(api_context):
    ctx = api_context
    group = await make_class(ctx, teacher=ctx.users.teacher)
    tid = await make_training(ctx, group, ctx.users.teacher)
    response = await ctx.client.patch(f"/api/v1/admin/classes/{group.id}", headers=headers(ctx.users.admin), json={"name": group.name, "expected_version": 0, "archived": False, "teacher_ids": [ctx.users.other_teacher.id], "transfer_to": ctx.users.other_teacher.id})
    assert response.status_code == 200 and response.json()["transferred"] == 1, response.text
    assert (await ctx.client.get(f"/api/v1/trainings/{tid}", headers=headers(ctx.users.teacher))).status_code == 404
    response = await ctx.client.get(f"/api/v1/trainings/{tid}", headers=headers(ctx.users.other_teacher))
    assert response.status_code == 200
    assert response.json()["original_teacher_id"] == ctx.users.teacher.id
    assert await ctx.session.get(TrainingMember, (tid, ctx.users.student.id)) is not None


async def test_archived_class_rejects_new_training_but_retains_read_access(api_context):
    ctx = api_context
    group = await make_class(ctx, teacher=ctx.users.teacher)
    tid = await make_training(ctx, group, ctx.users.teacher)
    group.archived = True
    await ctx.session.flush()
    response = await ctx.client.post("/api/v1/trainings", headers=headers(ctx.users.teacher), json={"title": "归档班任务", "class_name": group.name, "due_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(), "problems": [{"pid": ctx.problem.pid}]})
    assert response.status_code == 409
    assert (await ctx.client.get(f"/api/v1/trainings/{tid}", headers=headers(ctx.users.student))).status_code == 200


async def test_stale_version_and_foreign_teacher_rejected_without_audit(api_context):
    ctx = api_context
    group = await make_class(ctx)
    for payload in ({"name": "新名称", "expected_version": 99, "archived": False, "teacher_ids": [], "transfer_to": None},
                    {"name": "新名称", "expected_version": 0, "archived": False, "teacher_ids": [ctx.users.student.id], "transfer_to": None}):
        response = await ctx.client.patch(f"/api/v1/admin/classes/{group.id}", headers=headers(ctx.users.admin), json=payload)
        assert response.status_code in (409, 422)
    assert await ctx.session.scalar(select(func.count()).select_from(AdminAudit)) == 0


async def test_concurrent_admin_removal_cannot_disable_last_admin(api_context, isolated_database):
    sessions = async_sessionmaker(isolated_database, expire_on_commit=False)
    async with sessions() as session:
        people = [User(username=f"concurrent_admin_{i}", role="admin") for i in range(2)]
        session.add_all(people)
        await session.commit()
        ids = [row.id for row in people]

    async def remove(actor_id, target_id):
        async with sessions() as session:
            actor = await session.get(User, actor_id)
            request = Request({"type": "http"})
            request.state.token_version = 0
            try:
                await edit_account(target_id, AccountEdit(expected_version=0, display_name="管理员", role="admin", enabled=False), request, session, actor)
                return 200
            except HTTPException as exc:
                await session.rollback()
                return exc.status_code

    results = await asyncio.gather(remove(ids[0], ids[1]), remove(ids[1], ids[0]))
    assert sorted(results) == [200, 401]
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.enabled.is_(True))) == 1
        assert await session.scalar(select(func.count()).select_from(AdminAudit)) == 1


async def test_successful_import_and_class_roster_filter(api_context):
    ctx = api_context
    group = await make_class(ctx)
    rows = [{"username": "import-a", "display_name": "导入甲", "password": "initial-pass-123", "class_id": group.id},
            {"username": "import-b", "display_name": "导入乙", "password": "initial-pass-456"}]
    response = await ctx.client.post("/api/v1/admin/users/import", headers=headers(ctx.users.admin), json={"rows": rows})
    assert response.status_code == 200 and len(response.json()) == 2, response.text
    response = await ctx.client.get(f"/api/v1/admin/users?group_id={group.id}", headers=headers(ctx.users.admin))
    assert [r["username"] for r in response.json()["items"]] == ["import-a"]
    response = await ctx.client.get("/api/v1/admin/users?q=import-a", headers=headers(ctx.users.admin))
    assert len(response.json()["items"]) == 1
    assert await ctx.session.scalar(select(func.count()).select_from(AdminAudit)) == 2


@pytest.mark.parametrize("payload", [
    {"username": "white-name", "display_name": "   ", "password": "valid-pass-123"},
    {"username": "bad-password", "display_name": "姓名", "password": "bad\ud800password"},
])
async def test_invalid_account_fields_are_rejected_without_leaking_input(api_context, payload):
    ctx = api_context
    response = await ctx.client.post("/api/v1/admin/users", headers={**headers(ctx.users.admin), "Content-Type": "application/json"}, content=json.dumps(payload))
    assert response.status_code == 422
    assert "bad-password" not in response.text and "valid-pass-123" not in response.text
