"""独立 PostgreSQL 会话回归：持久任务、并发领取、失效写入与恢复。"""

import asyncio
import os
from contextlib import suppress
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from app.capabilities.sandbox import CommandResult, CompileResult, SandboxError
from app.capabilities.test_suite import JudgeSuite
from app.capabilities.triage import triage
from app.core.config import settings
from app.core.security import create_access_token
from app.domain.models import Base, Diagnosis, Evidence, JudgeJob, Problem, Submission, User
from app.infra.db import get_session
from app.main import app
from app.services.job_service import JobService, LeaseLost, require_lease
from app.services.submission_service import SubmissionService, VerdictResult
from app.worker import JudgeWorker


@pytest.fixture
async def jobs_context(api_context):
    schema = "judge_jobs_" + uuid4().hex
    engine = create_async_engine(
        os.environ["TEST_DATABASE_URL"], poolclass=NullPool,
        execution_options={"schema_translate_map": {None: schema}},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    previous = app.dependency_overrides.get(get_session)
    try:
        async with engine.begin() as connection:
            await connection.execute(CreateSchema(schema))
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as session:
            student = User(username="job_student", role="student", class_name="A", password_hash="unused")
            peer = User(username="job_peer", role="student", class_name="A", password_hash="unused")
            teacher = User(username="job_teacher", role="teacher", class_name="A", password_hash="unused")
            problem = Problem(pid="JOB-TEST", title="后台判题测试", samples=[{"input": "1", "output": "1"}])
            session.add_all([student, peer, teacher, problem])
            await session.commit()

        def headers(user=None):
            user = user or student
            return {"Authorization": "Bearer " + create_access_token(user.id, user.role, user.username)}

        async def independent_session():
            async with sessions() as session:
                yield session

        app.dependency_overrides[get_session] = independent_session
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://jobs") as client:
            yield SimpleNamespace(sessions=sessions, client=client, headers=headers,
                                  student=student, peer=peer, teacher=teacher, problem=problem)
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_session, None)
        else:
            app.dependency_overrides[get_session] = previous
        async with engine.begin() as connection:
            await connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        await engine.dispose()


class Runner:
    async def compile(self, code):
        return CompileResult(ok=True, stderr="", artifact_id="memory")

    async def run(self, artifact_id, **kwargs):
        return CommandResult(status="Accepted", exit_code=0, time_ns=1, memory_bytes=1, stdout="1")

    async def cleanup(self, artifact_id):
        pass


async def enqueue(ctx, code="int main(){return 0;}"):
    response = await ctx.client.post("/api/v1/submissions", headers={**ctx.headers(), "Prefer": "respond-async"},
                                     json={"problem_pid": ctx.problem.pid, "code": code})
    assert response.status_code == 202, response.text
    assert response.headers["Location"].endswith(str(response.json()["id"]))
    return response.json()


async def claim(ctx):
    async with ctx.sessions() as session:
        return await JobService(session).claim()


async def make_available(ctx, job_id, *, expire=False):
    async with ctx.sessions() as session:
        fields = {"available_at": func.now() - timedelta(seconds=1)}
        if expire:
            fields["lease_expires_at"] = func.now() - timedelta(seconds=1)
        await session.execute(update(JudgeJob).where(JudgeJob.id == job_id).values(**fields))
        await session.commit()


async def test_async_acceptance_is_durable_and_duplicate_reuses_one_job(jobs_context, monkeypatch):
    ctx = jobs_context

    class ForbiddenRunner(Runner):
        async def compile(self, code):
            raise AssertionError("202 接受请求不能同步运行判题")

    monkeypatch.setattr("app.services.submission_service.build_runner", ForbiddenRunner)
    first, duplicate = await enqueue(ctx), await enqueue(ctx)
    assert first["id"] == duplicate["id"]
    assert first["job"]["id"] == duplicate["job"]["id"]
    assert first["stage"] == "QUEUED" and first["verdict"] is None
    assert first["job"]["status"] == "queued" and first["job"]["attempts"] == 0
    async with ctx.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Submission)) == 1
        assert await session.scalar(select(func.count()).select_from(JudgeJob)) == 1
    denied = await ctx.client.get(f"/api/v1/submissions/{first['id']}", headers=ctx.headers(ctx.peer))
    assert denied.status_code == 404


async def test_worker_completes_and_polling_returns_the_saved_result(jobs_context, monkeypatch):
    ctx = jobs_context
    monkeypatch.setattr("app.services.submission_service.build_runner", Runner)
    queued = await enqueue(ctx)
    assert await JudgeWorker(ctx.sessions).run_once() is True
    assert await JudgeWorker(ctx.sessions).run_once() is False
    response = await ctx.client.get(f"/api/v1/submissions/{queued['id']}", headers=ctx.headers())
    result = response.json()
    assert result["stage"] == "READY" and result["verdict"] == "SAMPLE_PASS"
    assert result["job"]["status"] == "succeeded" and result["job"]["attempts"] == 1


async def test_two_workers_cannot_hold_the_same_lease(jobs_context):
    ctx = jobs_context
    await enqueue(ctx)
    leases = await asyncio.gather(claim(ctx), claim(ctx))
    assert sum(lease is not None for lease in leases) == 1


async def test_expired_owner_cannot_write_evidence_or_failure_over_a_new_attempt(jobs_context):
    ctx = jobs_context
    queued = await enqueue(ctx)
    old = await claim(ctx)
    async with ctx.sessions() as session:
        session.add(Evidence(submission_id=queued["id"], type="compile_log", payload={"old": True}))
        await session.commit()
    await make_available(ctx, queued["job"]["id"], expire=True)
    current = await claim(ctx)
    assert current.owner_token != old.owner_token and current.attempt == 2
    async with ctx.sessions() as session:
        submission = await session.get(Submission, queued["id"])
        with pytest.raises(LeaseLost):
            await SubmissionService(session, sandbox=Runner(), lease=old).run(submission)
    async with ctx.sessions() as session:
        assert await JobService(session).fail(old, "execution_failed") is False
    async with ctx.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Evidence)) == 0
        job = await session.get(JudgeJob, current.job_id)
        assert job.owner_token == current.owner_token and job.status == "running"
        submission = await session.get(Submission, queued["id"])
        await SubmissionService(session, sandbox=Runner(), lease=current).run(submission)
    result = await ctx.client.get(f"/api/v1/submissions/{queued['id']}", headers=ctx.headers())
    assert result.json()["job"]["status"] == "succeeded"


async def test_expired_heartbeat_cannot_resurrect_ownership(jobs_context):
    ctx = jobs_context
    queued = await enqueue(ctx)
    lease = await claim(ctx)
    await make_available(ctx, queued["job"]["id"], expire=True)
    async with ctx.sessions() as session:
        assert await JobService(session).renew(lease) is False
    recovered = await claim(ctx)
    assert recovered.owner_token != lease.owner_token


async def test_system_failure_retries_without_mixing_old_evidence(jobs_context, monkeypatch):
    ctx = jobs_context

    class BrokenRunner(Runner):
        async def compile(self, code):
            raise SandboxError("private-internal-service-detail")

    monkeypatch.setattr("app.services.submission_service.build_runner", BrokenRunner)
    queued = await enqueue(ctx)
    await JudgeWorker(ctx.sessions).run_once()
    waiting = (await ctx.client.get(f"/api/v1/submissions/{queued['id']}", headers=ctx.headers())).json()
    assert waiting["stage"] == "QUEUED" and waiting["verdict"] is None
    assert waiting["job"]["status"] == "queued" and waiting["job"]["attempts"] == 1
    assert "private-internal" not in waiting["job"]["last_error"]
    await make_available(ctx, queued["job"]["id"])
    monkeypatch.setattr("app.services.submission_service.build_runner", Runner)
    await JudgeWorker(ctx.sessions).run_once()
    async with ctx.sessions() as session:
        evidence = list((await session.scalars(select(Evidence))).all())
        assert sorted(row.type for row in evidence) == ["compile_log", "run_trace", "static_alert"]
    ready = (await ctx.client.get(f"/api/v1/submissions/{queued['id']}", headers=ctx.headers())).json()
    assert ready["job"]["status"] == "succeeded" and ready["job"]["attempts"] == 2


async def test_failed_job_retry_is_owned_bounded_and_does_not_change_parent(jobs_context, monkeypatch):
    ctx = jobs_context
    monkeypatch.setattr(settings, "judge_job_max_attempts", 1)
    monkeypatch.setattr(settings, "judge_job_max_manual_retries", 1)

    class BrokenRunner(Runner):
        async def compile(self, code):
            raise SandboxError("offline")

    monkeypatch.setattr("app.services.submission_service.build_runner", BrokenRunner)
    queued = await enqueue(ctx)
    worker = JudgeWorker(ctx.sessions)
    await worker.run_once()
    result = (await ctx.client.get(f"/api/v1/submissions/{queued['id']}", headers=ctx.headers())).json()
    assert result["job"]["status"] == "failed" and result["job"]["retryable"] is True
    retry_url = f"/api/v1/submissions/{queued['id']}/retry"
    assert (await ctx.client.post(retry_url, headers=ctx.headers(ctx.peer))).status_code == 404
    assert (await ctx.client.post(retry_url, headers=ctx.headers(ctx.teacher))).status_code == 403
    retried = await ctx.client.post(retry_url, headers=ctx.headers())
    assert retried.status_code == 202 and retried.json()["id"] == queued["id"]
    assert retried.json()["parent_submission_id"] == queued["parent_submission_id"]
    await worker.run_once()
    failed = (await ctx.client.get(f"/api/v1/submissions/{queued['id']}", headers=ctx.headers())).json()
    assert failed["job"]["retryable"] is False
    assert (await ctx.client.post(retry_url, headers=ctx.headers())).status_code == 409


async def test_job_timeout_is_a_system_failure_not_a_student_verdict(jobs_context, monkeypatch):
    ctx = jobs_context
    monkeypatch.setattr(settings, "judge_job_max_attempts", 1)
    monkeypatch.setattr(settings, "judge_job_timeout_seconds", 1)

    class HangingRunner(Runner):
        async def compile(self, code):
            await asyncio.Event().wait()

    monkeypatch.setattr("app.services.submission_service.build_runner", HangingRunner)
    queued = await enqueue(ctx)
    await JudgeWorker(ctx.sessions).run_once()
    result = (await ctx.client.get(f"/api/v1/submissions/{queued['id']}", headers=ctx.headers())).json()
    assert result["stage"] == "SANDBOX_ERROR" and result["verdict"] is None
    assert result["job"]["status"] == "failed" and "时限" in result["job"]["last_error"]


async def test_shutdown_requeues_the_owned_job(jobs_context, monkeypatch):
    ctx = jobs_context
    entered = asyncio.Event()

    class HangingRunner(Runner):
        async def compile(self, code):
            entered.set()
            await asyncio.Event().wait()

    monkeypatch.setattr("app.services.submission_service.build_runner", HangingRunner)
    queued = await enqueue(ctx)
    execution = asyncio.create_task(JudgeWorker(ctx.sessions).run_once())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
    finally:
        execution.cancel()
        with suppress(asyncio.CancelledError):
            await execution
    result = (await ctx.client.get(f"/api/v1/submissions/{queued['id']}", headers=ctx.headers())).json()
    assert result["job"]["status"] == "queued" and result["stage"] == "QUEUED"


async def test_late_recovery_does_not_replace_a_ready_teacher_review(jobs_context, monkeypatch):
    ctx = jobs_context
    monkeypatch.setattr("app.services.submission_service.build_runner", Runner)
    await enqueue(ctx)
    await JudgeWorker(ctx.sessions).run_once()
    async with ctx.sessions() as session:
        diagnosis = await session.scalar(select(Diagnosis))
        diagnosis.narrative = "教师已经根据实际证据核对。"
        diagnosis.reviewer_id = ctx.teacher.id
        await session.execute(update(JudgeJob).values(status="queued"))
        await session.commit()
    assert await claim(ctx) is None
    async with ctx.sessions() as session:
        assert (await session.scalar(select(Diagnosis))).narrative == "教师已经根据实际证据核对。"
        assert (await session.scalar(select(Submission))).verdict == "SAMPLE_PASS"


async def test_finalize_checks_owner_before_dirty_submission_can_autoflush(jobs_context):
    ctx = jobs_context
    queued = await enqueue(ctx)
    old = await claim(ctx)
    await make_available(ctx, old.job_id, expire=True)
    current = await claim(ctx)
    async with ctx.sessions() as session:
        submission = await session.get(Submission, queued["id"])
        with pytest.raises(LeaseLost):
            await SubmissionService(session, sandbox=Runner(), lease=old)._finalize(
                submission, VerdictResult("AC", 1, 1), triage(""),
                JudgeSuite("package", [], "test-digest"),
            )
        assert submission.stage == "COMPILING"
        assert submission.verdict is None
        await session.rollback()
    async with ctx.sessions() as session:
        assert (await session.get(JudgeJob, current.job_id)).owner_token == current.owner_token


async def test_lease_checks_use_wall_clock_even_in_an_old_transaction(jobs_context):
    ctx = jobs_context
    await enqueue(ctx)
    lease = await claim(ctx)
    async with ctx.sessions() as session:
        await session.scalar(select(func.now()))  # 固定本事务的 now()。
        async with ctx.sessions() as control:
            await control.execute(update(JudgeJob).where(JudgeJob.id == lease.job_id).values(
                lease_expires_at=func.clock_timestamp() + timedelta(milliseconds=50)
            ))
            await control.commit()
        await asyncio.sleep(0.1)
        with pytest.raises(LeaseLost):
            await require_lease(session, lease)


async def test_heartbeat_keeps_a_long_compile_owned(jobs_context, monkeypatch):
    ctx = jobs_context
    monkeypatch.setattr(settings, "judge_job_lease_seconds", 5)
    entered = asyncio.Event()

    class SlowRunner(Runner):
        async def compile(self, code):
            entered.set()
            await asyncio.sleep(6)
            return await super().compile(code)

    monkeypatch.setattr("app.services.submission_service.build_runner", SlowRunner)
    queued = await enqueue(ctx)
    execution = asyncio.create_task(JudgeWorker(ctx.sessions).run_once())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        await asyncio.sleep(5.2)
        assert await claim(ctx) is None
        await asyncio.wait_for(execution, timeout=5)
    finally:
        if not execution.done():
            execution.cancel()
            with suppress(asyncio.CancelledError):
                await execution
    response = await ctx.client.get(f"/api/v1/submissions/{queued['id']}", headers=ctx.headers())
    assert response.json()["job"]["status"] == "succeeded"


async def test_heartbeat_does_not_cancel_cleanup_after_success(jobs_context, monkeypatch):
    ctx = jobs_context
    monkeypatch.setattr(settings, "judge_job_lease_seconds", 5)
    cleanup_started = asyncio.Event()
    finish_cleanup = asyncio.Event()
    cleanup_cancelled = False

    class SlowCleanupRunner(Runner):
        async def cleanup(self, artifact_id):
            nonlocal cleanup_cancelled
            cleanup_started.set()
            try:
                await finish_cleanup.wait()
            except asyncio.CancelledError:
                cleanup_cancelled = True
                raise

    monkeypatch.setattr("app.services.submission_service.build_runner", SlowCleanupRunner)
    renew_calls = 0

    async def unavailable_after_commit(*args, **kwargs):
        nonlocal renew_calls
        renew_calls += 1
        raise RuntimeError("database unavailable after successful commit")

    monkeypatch.setattr(JobService, "renew", unavailable_after_commit)
    queued = await enqueue(ctx)
    execution = asyncio.create_task(JudgeWorker(ctx.sessions).run_once())
    try:
        await asyncio.wait_for(cleanup_started.wait(), timeout=5)
        async with ctx.sessions() as session:
            job = await session.get(JudgeJob, queued["job"]["id"])
            assert job.status == "succeeded" and job.owner_token
        await asyncio.sleep(2.1)  # 跨过一次心跳，清理应继续等待而非被误取消。
        assert not execution.done()
        assert cleanup_cancelled is False
        assert renew_calls == 0
    finally:
        finish_cleanup.set()
        await asyncio.wait_for(execution, timeout=5)
    assert cleanup_cancelled is False


async def test_worker_cancellation_survives_failure_to_save_shutdown_state(jobs_context, monkeypatch):
    ctx = jobs_context
    entered = asyncio.Event()

    class HangingRunner(Runner):
        async def compile(self, code):
            entered.set()
            await asyncio.Event().wait()

    async def unavailable_database(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("app.services.submission_service.build_runner", HangingRunner)
    monkeypatch.setattr(JobService, "fail", unavailable_database)
    await enqueue(ctx)
    worker = asyncio.create_task(JudgeWorker(ctx.sessions).run_forever())
    await asyncio.wait_for(entered.wait(), timeout=5)
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(worker, timeout=2)


async def test_queue_insert_failure_cannot_leave_an_orphan_submission(jobs_context):
    ctx = jobs_context
    async with ctx.sessions() as session:
        def invalidate_job(sync_session, flush_context, instances):
            for obj in sync_session.new:
                if isinstance(obj, JudgeJob):
                    obj.max_attempts = 0

        event.listen(session.sync_session, "before_flush", invalidate_job)
        try:
            with pytest.raises(IntegrityError):
                await SubmissionService(session, sandbox=Runner()).create(
                    ctx.problem.pid, "int main(){}", ctx.student.id, background=True
                )
            await session.rollback()
        finally:
            event.remove(session.sync_session, "before_flush", invalidate_job)
    async with ctx.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Submission)) == 0
        assert await session.scalar(select(func.count()).select_from(JudgeJob)) == 0
