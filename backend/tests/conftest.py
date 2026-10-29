"""可选 PostgreSQL 集成环境：TEST_DATABASE_URL 必须指向独立测试库。"""

from __future__ import annotations

import os
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.security import create_access_token
from app.domain.models import Diagnosis, Problem, Submission, User
from app.infra.db import get_session
from app.main import app


@pytest.fixture
async def api_context(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("设置 TEST_DATABASE_URL 后运行 PostgreSQL HTTP 集成测试")
    if "test" not in (make_url(url).database or "").lower():
        pytest.fail("TEST_DATABASE_URL 必须指向名称含 test 的独立测试数据库")
    monkeypatch.setattr(settings, "llm_enabled", False)
    monkeypatch.setattr(settings, "jwt_secret", "integration-test-secret-is-at-least-32-bytes-long")
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )

        async def override_session():
            yield session

        app.dependency_overrides[get_session] = override_session
        users = SimpleNamespace()
        for name, role, group in [
            ("student", "student", "A"), ("peer", "student", "A"),
            ("other", "student", "B"), ("teacher", "teacher", "A"),
            ("other_teacher", "teacher", "B"), ("blank_teacher", "teacher", ""),
            ("admin", "admin", ""),
        ]:
            row = User(username=f"integration_{name}", display_name=name, role=role,
                       password_hash="unused", class_name=group)
            session.add(row)
            setattr(users, name, row)
        problem = Problem(pid="TEST-ACCESS", title="独立集成测试题", samples=[], package_level="C")
        session.add(problem)
        await session.flush()

        def headers(user=None):
            user = user or users.student
            return {"Authorization": "Bearer " + create_access_token(user.id, user.role, user.username)}

        async def submission(user=None, target_problem=None, **kwargs):
            user = user or users.student
            target_problem = target_problem or problem
            unique = len(session.identity_map)
            row = Submission(
                problem_id=target_problem.id, student_id=user.id,
                code=kwargs.pop("code", f"int main(){{return {unique};}}"),
                code_hash=kwargs.pop("code_hash", f"fixture-hash-{unique}"),
                verdict=kwargs.pop("verdict", "WA"), stage=kwargs.pop("stage", "READY"),
                tests_passed=kwargs.pop("tests_passed", {"passed": 0, "total": 1, "scope": "package"}),
                **kwargs,
            )
            session.add(row)
            await session.flush()
            session.add(Diagnosis(submission_id=row.id, narrative="请对照执行证据检查输出。"))
            await session.flush()
            return row

        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://integration"
            ) as client:
                yield SimpleNamespace(client=client, session=session, users=users, problem=problem,
                                      headers=headers, submission=submission)
        finally:
            app.dependency_overrides.pop(get_session, None)
            await session.close()
            await transaction.rollback()
    await engine.dispose()
