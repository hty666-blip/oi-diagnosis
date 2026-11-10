"""创建班级同时分配教练和导入学生，失败时不遗留半成品。"""
import httpx
import pytest
from sqlalchemy import func, select

from app.domain.models import AdminAudit, ClassTeacher, SchoolClass, User


async def test_duplicate_single_account_has_actionable_error(api_context):
    ctx = api_context
    response = await ctx.client.post("/api/v1/admin/users", headers=ctx.headers(ctx.users.admin), json={
        "username": ctx.users.student.username, "display_name": "重复账号", "password": "test-temp-pass-123",
    })
    assert response.status_code == 409
    assert response.json()["detail"] == "用户名重复或已存在"


async def test_mid_import_failure_rolls_back_class_roster_and_audits(isolated_database, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.api.v1 import admin
    from app.core.security import create_access_token
    from app.infra.db import get_session
    from app.main import app

    factory = async_sessionmaker(isolated_database, expire_on_commit=False)
    async with factory() as session:
        owner = User(username="isolatedadmin", role="admin", display_name="测试管理员")
        coach = User(username="isolatedcoach", role="teacher", display_name="测试教练")
        session.add_all([owner, coach])
        await session.commit()
        token = create_access_token(owner.id, owner.role, owner.username)
        coach_id = coach.id

    async def isolated_session():
        async with factory() as session:
            yield session

    original = app.dependency_overrides[get_session]
    app.dependency_overrides[get_session] = isolated_session
    calls = 0

    def fail_second_hash(password):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("模拟第二行处理失败")
        return "test-hash"

    monkeypatch.setattr(admin, "hash_password", fail_second_hash)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://integration") as client:
            response = await client.post("/api/v1/admin/classes", headers={"Authorization": f"Bearer {token}"}, json={
                "name": "必须整批回滚", "teacher_ids": [coach_id],
                "student_accounts": [{"username": f"new-{i}", "display_name": f"新同学{i}", "password": "test-temp-pass"} for i in range(2)],
            })
        assert response.status_code == 500 and calls == 2
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(SchoolClass)) == 0
            assert await session.scalar(select(func.count()).select_from(ClassTeacher)) == 0
            assert await session.scalar(select(func.count()).select_from(AdminAudit)) == 0
            assert await session.scalar(select(func.count()).select_from(User)) == 2
    finally:
        app.dependency_overrides[get_session] = original


async def test_create_class_with_teachers_and_students(api_context):
    ctx = api_context
    response = await ctx.client.post("/api/v1/admin/classes", headers=ctx.headers(ctx.users.admin), json={
        "name": " 一起创建班 ", "teacher_ids": [ctx.users.teacher.id],
        "student_accounts": [{"username": "newmember", "display_name": "新同学", "password": "class-temp-password"}],
    })
    assert response.status_code == 200, response.text
    cid = response.json()["id"]
    student = await ctx.session.scalar(select(User).where(User.username == "newmember"))
    assert student.class_id == cid and student.class_name == "一起创建班"
    assert student.must_change_password and student.role == "student"
    assert await ctx.session.scalar(select(ClassTeacher.teacher_id).where(ClassTeacher.class_id == cid)) == ctx.users.teacher.id
    rows = (await ctx.session.scalars(select(AdminAudit))).all()
    assert {row.action for row in rows} == {"class.create", "account.create"}
    assert "class-temp-password" not in str([row.after for row in rows])


@pytest.mark.parametrize("case", ["duplicate", "existing", "teacher", "other_class", "disabled_teacher", "repeated_teacher"])
async def test_invalid_class_roster_creates_nothing(api_context, case):
    ctx = api_context
    student = {"username": "freshmember", "display_name": "新同学", "password": "class-temp-password"}
    payload = {"name": "不应留下的班级", "teacher_ids": [ctx.users.teacher.id], "student_accounts": [student]}
    if case == "duplicate":
        payload["student_accounts"] = [student, student]
    elif case == "existing":
        payload["student_accounts"] = [student, {**student, "username": ctx.users.student.username}]
    elif case == "teacher":
        payload["student_accounts"] = [{**student, "role": "teacher"}]
    elif case == "other_class":
        payload["student_accounts"] = [{**student, "class_id": 123}]
    elif case == "disabled_teacher":
        ctx.users.teacher.enabled = False
        await ctx.session.flush()
    else:
        payload["teacher_ids"] *= 2
    response = await ctx.client.post("/api/v1/admin/classes", headers=ctx.headers(ctx.users.admin), json=payload)
    assert response.status_code in (409, 422), response.text
    assert await ctx.session.scalar(select(SchoolClass.id).where(SchoolClass.name == payload["name"])) is None
    assert await ctx.session.scalar(select(User.id).where(User.username == "freshmember")) is None
    assert await ctx.session.scalar(select(func.count()).select_from(AdminAudit)) == 0


async def test_old_class_create_contract_and_permissions(api_context):
    ctx = api_context
    response = await ctx.client.post("/api/v1/admin/classes", headers=ctx.headers(), json={"name": "禁止学生创建"})
    assert response.status_code == 403
    response = await ctx.client.post("/api/v1/admin/classes", headers=ctx.headers(ctx.users.admin), json={"name": "先建空班"})
    assert response.status_code == 200
