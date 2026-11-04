import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from app.domain.models import ClassTeacher, SchoolClass, User


async def test_demo_script_rejects_production_before_opening_database(monkeypatch):
    location = Path(__file__).resolve().parents[2] / "scripts" / "seed_demo.py"
    spec = importlib.util.spec_from_file_location("guarded_demo_script", location)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.settings, "app_env", "production")

    def forbidden():
        raise AssertionError("生产环境不应打开数据库来生成公开演示账号")

    monkeypatch.setattr(module, "SessionLocal", forbidden)
    with pytest.raises(RuntimeError, match="只允许"):
        await module.ensure_users()


async def test_fresh_demo_seed_uses_stable_classes_and_preserves_admin_changes(api_context, monkeypatch):
    ctx = api_context
    location = Path(__file__).resolve().parents[2] / "scripts" / "seed_demo.py"
    spec = importlib.util.spec_from_file_location("managed_demo_script", location)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.settings, "app_env", "dev")

    @asynccontextmanager
    async def local_session():
        yield ctx.session

    monkeypatch.setattr(module, "SessionLocal", local_session)
    await module.ensure_users()
    teacher = await ctx.session.scalar(select(User).where(User.username == "teacher01"))
    student = await ctx.session.scalar(select(User).where(User.username == "student01"))
    classes = (await ctx.session.scalars(select(SchoolClass))).all()
    assert len(classes) == 3 and student.class_id is not None
    assert await ctx.session.get(ClassTeacher, (student.class_id, teacher.id)) is not None
    # 重跑初始化不得重新分班、恢复已解除的教练关系或覆盖密码。
    await ctx.session.execute(delete(ClassTeacher).where(ClassTeacher.teacher_id == teacher.id))
    student.class_id, student.class_name = None, ""
    teacher.password_hash = "administrator-updated-hash"
    teacher.display_name = "管理员改名后的教练"
    await ctx.session.commit()
    await module.ensure_users()
    assert student.class_id is None and student.class_name == ""
    assert teacher.display_name == "管理员改名后的教练"
    assert teacher.password_hash == "administrator-updated-hash"
    assert not (await ctx.session.scalars(select(ClassTeacher).where(ClassTeacher.teacher_id == teacher.id))).all()
