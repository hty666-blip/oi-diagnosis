"""运维验收：真实 MySQL、离线模型、隔离恢复库，禁止触碰业务数据。"""
import asyncio
import copy
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import inspect, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from app.core.config import settings
from app.domain.models import (
    AdminAudit,
    AICallEvent,
    AIConfiguration,
    AIConfigurationCheck,
    BackupArtifact,
    JudgeJob,
    MaintenanceTask,
    Problem,
    ProblemVersion,
    Submission,
    User,
    WorkerHeartbeat,
)
from app.gateway.llm import LLMClient
from app.maintenance_worker import MaintenanceWorker
from app.services.backup_service import MAGIC, BackupError, BackupIO
from app.services.problem_versions import version_digest
from app.services.runtime_ai import decrypt_key, runtime_client
from app.services.worker_monitor import WorkerMonitor, worker_status

BASE = "/api/v1/admin/runtime"


@pytest.mark.parametrize("path", ["judging", "ai", "backups"])
async def test_runtime_is_admin_only(api_context, path):
    ctx = api_context
    for user in (ctx.users.student, ctx.users.teacher):
        assert (await ctx.client.get(BASE + "/" + path, headers=ctx.headers(user))).status_code == 403
    result = await ctx.client.get(BASE + "/" + path, headers=ctx.headers(ctx.users.admin))
    assert result.status_code == 200, result.text


async def test_job_action_checks_state_preserves_reviews_and_audits(api_context):
    ctx = api_context
    sub = await ctx.submission(stage="FAILED", verdict=None)
    job = JudgeJob(submission_id=sub.id, status="failed", attempts=3, last_error="PRIVATE-KEY")
    ctx.session.add(job)
    await ctx.session.flush()
    headers = ctx.headers(ctx.users.admin)
    data = (await ctx.client.get(BASE + "/judging?state=failed&q=TEST", headers=headers)).json()
    assert len(data["items"]) == 1 and "PRIVATE-KEY" not in json.dumps(data)
    body = {"status": "failed", "attempts": 2, "manual_retries": 0}
    url = f"{BASE}/judging/{sub.id}/retry"
    assert (await ctx.client.post(url, headers=headers, json=body)).status_code == 409
    body["attempts"] = 3
    response = await ctx.client.post(url, headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "queued"
    assert await ctx.session.scalar(select(AdminAudit.id).where(AdminAudit.action == "judge.retry"))
    body = {"status": "queued", "attempts": 0, "manual_retries": 1}
    response = await ctx.client.post(f"{BASE}/judging/{sub.id}/cancel", headers=headers, json=body)
    assert response.status_code == 200 and response.json()["status"] == "cancelled"
    sub.stage = "READY"
    job.status = "failed"
    await ctx.session.flush()
    body["status"] = "failed"
    assert (await ctx.client.post(url, headers=headers, json=body)).status_code == 409


async def test_worker_heartbeat_fresh_stale_and_stopped(api_context, isolated_database):
    sessions = async_sessionmaker(isolated_database, expire_on_commit=False)
    monitor = WorkerMonitor(sessions, "judge")
    monitor.state = "busy"
    monitor.current_job = "submission:12"
    await monitor.pulse()
    async with sessions() as session:
        state = await worker_status(session)
        assert state["items"][0]["online"] and state["items"][0]["current_job"] == "submission:12"
        await session.execute(update(WorkerHeartbeat).values(seen_at=datetime.now(UTC) - timedelta(seconds=90)))
        await session.commit()
        assert not (await worker_status(session))["items"][0]["online"]
    monitor.state = "stopped"
    await monitor.pulse()
    async with sessions() as session:
        assert not (await worker_status(session))["items"][0]["online"]


def mock_ai(monkeypatch, *, success=True):
    monkeypatch.setattr(settings, "llm_enabled", True)
    monkeypatch.setattr(settings, "llm_api_base", "https://model.invalid/v1")
    monkeypatch.setattr(settings, "llm_api_key", "private-original-key")
    monkeypatch.setattr(settings, "llm_model_reasoning", "test-model")
    def respond(request):
        assert request.headers["Authorization"].startswith("Bearer ")
        if not success:
            return httpx.Response(401, text="private-original-key echoed by provider")
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})
    def factory(**kwargs):
        return LLMClient(transport=httpx.MockTransport(respond), **kwargs)
    monkeypatch.setattr("app.services.runtime_ai.LLMClient", factory)
    return factory


async def test_ai_check_activate_disable_and_unknown_tokens(api_context, monkeypatch):
    ctx = api_context
    factory = mock_ai(monkeypatch)
    headers = ctx.headers(ctx.users.admin)
    body = {"expected_version": 0, "base_url": settings.llm_api_base, "model": "next-model", "api_key": "candidate-private-key"}
    response = await ctx.client.post(BASE + "/ai/check", headers=headers, json=body)
    assert response.status_code == 200 and response.json()["ok"], response.text
    assert await ctx.session.get(AIConfiguration, 1) is None
    check = await ctx.session.get(AIConfigurationCheck, response.json()["check_id"])
    assert check.encrypted_key != body["api_key"] and decrypt_key(check.encrypted_key) == body["api_key"]
    response = await ctx.client.post(BASE + "/ai/activate", headers=headers, json={"check_id": check.id})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["enabled"] and data["version"] == 1 and data["model"] == "next-model"
    assert "private-key" not in response.text and data["usage"]["prompt_tokens"] is None
    assert data["usage"]["unknown_usage"] == 1
    assert (await runtime_client(ctx.session, factory=factory)).model == "next-model"
    response = await ctx.client.post(BASE + "/ai/disable", headers=headers, json={"expected_version": 1})
    assert response.status_code == 200 and not response.json()["enabled"]
    assert await runtime_client(ctx.session, factory=factory) is None
    assert (await ctx.client.post(BASE + "/ai/activate", headers=headers, json={"check_id": check.id})).status_code == 409
    assert await ctx.session.scalar(select(AICallEvent.id))


async def test_ai_failed_check_preserves_config_and_never_echoes_provider(api_context, monkeypatch):
    ctx = api_context
    mock_ai(monkeypatch, success=False)
    headers = ctx.headers(ctx.users.admin)
    body = {"expected_version": 0, "base_url": settings.llm_api_base, "model": "test-model"}
    response = await ctx.client.post(BASE + "/ai/check", headers=headers, json=body)
    assert response.status_code == 200 and not response.json()["ok"]
    assert response.json()["error"] == "http_401" and "private-original-key" not in response.text
    assert await ctx.session.get(AIConfiguration, 1) is None
    assert (await ctx.client.post(BASE + "/ai/activate", headers=headers, json={"check_id": response.json()["check_id"]})).status_code == 409
    body["base_url"] = "https://other.invalid/v1"
    assert (await ctx.client.post(BASE + "/ai/check", headers=headers, json=body)).status_code == 422
    body["api_key"] = "different-key"
    body["expected_version"] = 9
    assert (await ctx.client.post(BASE + "/ai/check", headers=headers, json=body)).status_code == 409


@pytest.mark.parametrize("address", ["http://remote.invalid", "https://user:pass@host.invalid", "https://host.invalid?key=secret", "https://host.invalid:bad"])
async def test_invalid_model_addresses_rejected(api_context, address):
    ctx = api_context
    response = await ctx.client.post(BASE + "/ai/check", headers=ctx.headers(ctx.users.admin), json={"expected_version": 0, "base_url": address, "model": "test"})
    assert response.status_code == 422


async def test_candidate_expires_and_deployment_switch_wins(api_context, monkeypatch):
    ctx = api_context
    mock_ai(monkeypatch)
    headers = ctx.headers(ctx.users.admin)
    response = await ctx.client.post(BASE + "/ai/check", headers=headers, json={"expected_version": 0, "base_url": settings.llm_api_base, "model": "test"})
    check = await ctx.session.get(AIConfigurationCheck, response.json()["check_id"])
    check.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await ctx.session.flush()
    assert (await ctx.client.post(BASE + "/ai/activate", headers=headers, json={"check_id": check.id})).status_code == 409
    monkeypatch.setattr(settings, "llm_enabled", False)
    assert await runtime_client(ctx.session) is None
    assert (await ctx.client.post(BASE + "/ai/check", headers=headers, json={"expected_version": 0, "base_url": settings.llm_api_base, "model": "test"})).status_code == 409


async def test_backup_enqueue_idempotence_and_permission(api_context):
    ctx = api_context
    headers = ctx.headers(ctx.users.admin)
    body = {"request_id": str(uuid4())}
    a = await ctx.client.post(BASE + "/backups/create", headers=headers, json=body)
    b = await ctx.client.post(BASE + "/backups/create", headers=headers, json=body)
    assert a.status_code == b.status_code == 202 and a.json()["id"] == b.json()["id"]
    assert (await ctx.client.post(BASE + "/backups/create", headers=ctx.headers(), json=body)).status_code == 403
    assert (await ctx.client.get(f'{BASE}/backups/{a.json()["artifact_id"]}/download', headers=headers)).status_code == 404


@pytest.fixture
async def restore_target(monkeypatch):
    url = make_url(os.environ["TEST_DATABASE_URL"])
    name = "test_restore_" + uuid4().hex
    engine = create_async_engine(url)
    monkeypatch.setattr(settings, "database_url", url.render_as_string(hide_password=False))
    target = url.set(database=name).render_as_string(hide_password=False)
    async with engine.begin() as conn:
        await conn.execute(CreateSchema(name))
    try:
        yield target
    finally:
        async with engine.begin() as conn:
            await conn.execute(DropSchema(name))
        await engine.dispose()


async def test_encrypted_backup_full_restore_and_cleanup(api_context, isolated_database, restore_target, tmp_path, monkeypatch):
    sessions = async_sessionmaker(isolated_database, expire_on_commit=False)
    async with sessions() as session:
        user = User(username="backup-private-student", password_hash="private-hash", role="student", token_version=3)
        problem = Problem(pid="BACKUP-1", title="恢复测试")
        session.add_all([user, problem])
        session.add_all([Problem(pid=f"PAGE-{i}", title="分页恢复记录") for i in range(53)])
        await session.flush()
        version = ProblemVersion(problem_id=problem.id, revision=1, data={"title": "冻结题目"}, suite=None, reviewer="", digest=version_digest({"title": "冻结题目"}, None, ""))
        session.add(version)
        await session.flush()
        sub = Submission(problem_id=problem.id, student_id=user.id, code="private student source", code_hash="test", stage="JUDGING", problem_version_id=version.id)
        session.add(sub)
        await session.flush()
        session.add(JudgeJob(submission_id=sub.id, status="running", owner_token="stale-owner", lease_expires_at=datetime.now(UTC) + timedelta(seconds=30)))
        await session.commit()
    io = BackupIO(source_engine=isolated_database, directory=tmp_path / "private/backups")
    with monkeypatch.context() as bounded:
        bounded.setattr("app.services.backup_service.MAX_BYTES", 8)
        with pytest.raises(BackupError, match="backup_too_large"):
            await io.snapshot()
    result = await io.create(str(uuid4()), str(uuid4()))
    archive = io.path(result["file_name"])
    assert b"private student source" not in archive.read_bytes() and b"private-hash" not in archive.read_bytes()
    payload = io.read(archive, result["sha256"])
    assert "ai_configuration" not in payload["tables"]
    validated = await io.restore(payload, restore_target)
    assert validated["content_verified"] and validated["counts"]["submission"] == 1
    assert validated["counts"]["problem"] == 54
    engine = create_async_engine(restore_target)
    try:
        async with engine.connect() as conn:
            assert await conn.run_sync(lambda c: inspect(c).get_table_names()) == []
        await io.restore(payload, restore_target, keep=True)
        async with engine.connect() as conn:
            assert await conn.scalar(select(User.token_version)) == 4
            assert await conn.scalar(select(JudgeJob.status)) == "queued"
            assert await conn.scalar(select(JudgeJob.owner_token)) is None
            assert not await conn.scalar(select(AIConfiguration.enabled))
            assert await conn.scalar(text("SELECT COUNT(*) FROM information_schema.triggers WHERE trigger_schema=DATABASE()")) == 2
        with pytest.raises(BackupError, match="verify_target_not_empty"):
            await io.restore(payload, restore_target)
        bad = bytearray(archive.read_bytes())
        bad[-1] ^= 1
        archive.write_bytes(bad)
        with pytest.raises(BackupError, match="archive_changed"):
            io.read(archive, result["sha256"])
    finally:
        await engine.dispose()


async def test_restore_rejects_unsafe_targets(api_context):
    io = BackupIO()
    source = make_url(settings.database_url)
    for target in (settings.database_url, source.set(database="production_copy").render_as_string(hide_password=False), source.set(host="other.invalid", database="test_restore_x").render_as_string(hide_password=False)):
        with pytest.raises(BackupError, match="verify_target_unsafe"):
            io.verify_url(target)


def test_backup_decode_does_not_create_a_missing_key(tmp_path):
    io = BackupIO(directory=tmp_path / "archives", key_path=tmp_path / "lost.key")
    archive = tmp_path / "saved.oibak"
    archive.write_bytes(MAGIC + b"x" * 40)
    with pytest.raises(BackupError, match="backup_key_unavailable"):
        io.read(archive)
    assert not io.key_path.exists()


async def test_maintenance_reclaims_expired_and_fences_old_writer(api_context, isolated_database):
    sessions = async_sessionmaker(isolated_database, expire_on_commit=False)
    async with sessions() as session:
        admin = User(username="backup-admin", role="admin", password_hash="unused")
        session.add(admin)
        await session.flush()
        artifact = BackupArtifact(id=str(uuid4()), created_by=admin.id)
        session.add(artifact)
        await session.flush()
        task = MaintenanceTask(id=str(uuid4()), request_id=str(uuid4()), kind="backup", artifact_id=artifact.id, actor_id=admin.id, actor_version=0,
                               status="running", owner=str(uuid4()), attempts=1, lease_until=datetime.now(UTC) - timedelta(seconds=1))
        session.add(task)
        await session.commit()
    reached, release = asyncio.Event(), asyncio.Event()
    class IO:
        async def create(self, *args):
            reached.set()
            await release.wait()
            return {"file_name": "must-not-publish", "size": 15}
    worker = MaintenanceWorker(sessions, IO())
    task_id, owner = await worker.claim()
    assert task_id == task.id and owner != task.owner
    execution = asyncio.create_task(worker.execute(task_id, owner))
    await reached.wait()
    async with sessions() as session:
        await session.execute(update(MaintenanceTask).values(owner=str(uuid4())))
        await session.commit()
    release.set()
    await execution
    async with sessions() as session:
        assert (await session.get(BackupArtifact, artifact.id)).file_name == ""
        assert (await session.get(MaintenanceTask, task.id)).attempts == 2


async def test_restore_bad_rows_clean_target(api_context, isolated_database, restore_target, tmp_path):
    io = BackupIO(source_engine=isolated_database, directory=tmp_path / "backups")
    raw, _, _ = await io.snapshot()
    payload = json.loads(raw)
    bad = copy.deepcopy(payload)
    bad["tables"]["problem"].append({"unknown_column": "broken"})
    with pytest.raises(SQLAlchemyError):
        await io.restore(bad, restore_target)
    engine = create_async_engine(restore_target)
    try:
        async with engine.connect() as conn:
            assert await conn.run_sync(lambda c: inspect(c).get_table_names()) == []
    finally:
        await engine.dispose()
