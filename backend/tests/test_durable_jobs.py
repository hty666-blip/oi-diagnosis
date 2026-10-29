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
            admin = User(username="job_admin", role="admin", class_name="", password_hash="unused")
            problem = Problem(pid="JOB-TEST", title="后台判题测试", samples=[{"input": "1", "output": "1"}])
            session.add_all([student, peer, teacher, admin, problem])
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
                                  student=student, peer=peer, teacher=teacher, admin=admin, problem=problem)
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


async def test_queued_cancel_is_owned_idempotent_and_keeps_code_and_lineage(jobs_context):
    ctx = jobs_context
    queued = await enqueue(ctx)
    url = f"/api/v1/submissions/{queued['id']}/cancel"
    assert queued["job"]["cancelable"] is True
    assert (await ctx.client.post(url, headers=ctx.headers(ctx.peer))).status_code == 404
    assert (await ctx.client.post(url, headers=ctx.headers(ctx.teacher))).status_code == 403
    teacher_view = await ctx.client.get(f"/api/v1/submissions/{queued['id']}", headers=ctx.headers(ctx.teacher))
    assert teacher_view.json()["job"]["cancelable"] is False
    cancelled = await ctx.client.post(url, headers=ctx.headers())
    assert cancelled.status_code == 200, cancelled.text
    body = cancelled.json()
    assert body["stage"] == "CANCELLED" and body["verdict"] is None
    assert body["job"]["status"] == "cancelled"
    assert body["job"]["cancelable"] is False and body["job"]["retryable"] is False
    assert body["code"] == queued["code"] and body["parent_submission_id"] == queued["parent_submission_id"]
    repeated = await ctx.client.post(url, headers=ctx.headers())
    assert repeated.status_code == 200 and repeated.json()["id"] == body["id"]
    assert await JudgeWorker(ctx.sessions).run_once() is False
    assert (await ctx.client.post(f"/api/v1/submissions/{queued['id']}/retry", headers=ctx.headers())).status_code == 409
    new = await ctx.client.post("/api/v1/submissions", headers={**ctx.headers(), "Prefer": "respond-async"},
                                json={"problem_pid": ctx.problem.pid, "code": body["code"],
                                      "parent_submission_id": body["id"]})
    assert new.status_code == 202 and new.json()["id"] != body["id"]
    assert new.json()["parent_submission_id"] == body["id"]
    lineage = await ctx.client.get(f"/api/v1/submissions/{new.json()['id']}/lineage", headers=ctx.headers())
    assert [row["id"] for row in lineage.json()] == [body["id"], new.json()["id"]]


async def test_admin_may_cancel_another_students_job(jobs_context):
    ctx = jobs_context
    queued = await enqueue(ctx)
    admin_view = await ctx.client.get(f"/api/v1/submissions/{queued['id']}", headers=ctx.headers(ctx.admin))
    assert admin_view.json()["job"]["cancelable"] is True
    cancelled = await ctx.client.post(f"/api/v1/submissions/{queued['id']}/cancel", headers=ctx.headers(ctx.admin))
    assert cancelled.status_code == 200 and cancelled.json()["job"]["status"] == "cancelled"


async def test_running_cancel_revokes_old_lease_and_refuses_all_old_writes(jobs_context):
    ctx = jobs_context
    queued = await enqueue(ctx)
    lease = await claim(ctx)
    cancelled = await ctx.client.post(f"/api/v1/submissions/{queued['id']}/cancel", headers=ctx.headers())
    assert cancelled.status_code == 200 and cancelled.json()["stage"] == "CANCELLED"
    async with ctx.sessions() as session:
        assert await JobService(session).renew(lease) is False
        assert await JobService(session).fail(lease, "execution_failed") is False
    async with ctx.sessions() as session:
        submission = await session.get(Submission, queued["id"])
        with pytest.raises(LeaseLost):
            await SubmissionService(session, sandbox=Runner(), lease=lease).run(submission)
    async with ctx.sessions() as session:
        job = await session.get(JudgeJob, lease.job_id)
        assert job.status == "cancelled" and job.owner_token is None and job.lease_expires_at is None
        assert (await session.get(Submission, queued["id"])).stage == "CANCELLED"
        assert await session.scalar(select(func.count()).select_from(Evidence)) == 0


async def test_cancel_does_not_replace_completed_or_failed_results(jobs_context, monkeypatch):
    ctx = jobs_context
    monkeypatch.setattr("app.services.submission_service.build_runner", Runner)
    queued = await enqueue(ctx)
    await JudgeWorker(ctx.sessions).run_once()
    denied = await ctx.client.post(f"/api/v1/submissions/{queued['id']}/cancel", headers=ctx.headers())
    assert denied.status_code == 409
    completed = (await ctx.client.get(f"/api/v1/submissions/{queued['id']}", headers=ctx.headers())).json()
    assert completed["stage"] == "READY" and completed["verdict"] == "SAMPLE_PASS"
    assert completed["job"]["status"] == "succeeded" and completed["job"]["cancelable"] is False
    monkeypatch.setattr(settings, "judge_job_max_attempts", 1)

    class BrokenRunner(Runner):
        async def compile(self, code):
            raise SandboxError("offline")

    monkeypatch.setattr("app.services.submission_service.build_runner", BrokenRunner)
    failed = await enqueue(ctx, "int main(){return 2;}")
    await JudgeWorker(ctx.sessions).run_once()
    assert (await ctx.client.post(f"/api/v1/submissions/{failed['id']}/cancel", headers=ctx.headers())).status_code == 409


@pytest.mark.parametrize("legacy_stage", ["RECEIVED", "COMPILING", "RUNNING"])
async def test_background_request_skips_old_inline_activity_without_modifying_it(jobs_context, legacy_stage):
    ctx = jobs_context
    async with ctx.sessions() as session:
        service = SubmissionService(session, sandbox=Runner())
        parent = await service.create(ctx.problem.pid, "int main(){return 1;}", ctx.student.id)
        await service.run(parent)
        old = await service.create(ctx.problem.pid, "int main(){return 2;}", ctx.student.id, parent.id)
        old.stage = legacy_stage
        await session.commit()
        old_id, parent_id = old.id, parent.id
    body = {"problem_pid": ctx.problem.pid, "code": "int main(){return 2;}", "parent_submission_id": parent_id}
    headers = {**ctx.headers(), "Prefer": "respond-async"}
    new = await ctx.client.post("/api/v1/submissions", headers=headers, json=body)
    assert new.status_code == 202, new.text
    assert new.json()["id"] != old_id and new.json()["parent_submission_id"] == parent_id
    repeated = await ctx.client.post("/api/v1/submissions", headers=headers, json=body)
    assert repeated.status_code == 202 and repeated.json()["id"] == new.json()["id"]
    async with ctx.sessions() as session:
        original = await session.get(Submission, old_id)
        assert original.stage == legacy_stage and original.parent_submission_id == parent_id
        assert await session.scalar(select(func.count()).select_from(JudgeJob)) == 1
    assert (await ctx.client.post(f"/api/v1/submissions/{old_id}/cancel", headers=ctx.headers())).status_code == 409


async def test_background_request_still_reuses_ready_inline_cache(jobs_context):
    ctx = jobs_context
    async with ctx.sessions() as session:
        service = SubmissionService(session, sandbox=Runner())
        ready = await service.create(ctx.problem.pid, "int main(){return 0;}", ctx.student.id)
        await service.run(ready)
        ready_id = ready.id
    result = await ctx.client.post("/api/v1/submissions", headers={**ctx.headers(), "Prefer": "respond-async"},
                                   json={"problem_pid": ctx.problem.pid, "code": "int main(){return 0;}"})
    assert result.status_code == 200 and result.json()["id"] == ready_id
    assert result.json()["stage"] == "READY" and result.json()["job"] is None


@pytest.mark.parametrize("winner", ["cancel", "complete"])
async def test_cancel_and_completion_have_exactly_one_terminal_winner(jobs_context, winner):
    ctx = jobs_context
    queued = await enqueue(ctx)
    lease = await claim(ctx)

    async def finish_elsewhere():
        async with ctx.sessions() as session:
            submission = await session.get(Submission, queued["id"])
            try:
                await SubmissionService(session, sandbox=Runner(), lease=lease)._finalize(
                    submission, VerdictResult("SAMPLE_PASS", 1, 1), triage(""),
                    JudgeSuite("samples", [], "race-digest"),
                )
                return "completed"
            except LeaseLost:
                return "lease_lost"

    async with ctx.sessions() as session:
        await session.scalar(select(JudgeJob).where(JudgeJob.id == lease.job_id).with_for_update())
        if winner == "cancel":
            competing = asyncio.create_task(finish_elsewhere())
        else:
            competing = asyncio.create_task(ctx.client.post(
                f"/api/v1/submissions/{queued['id']}/cancel", headers=ctx.headers()
            ))
        await asyncio.sleep(0.05)
        assert not competing.done()
        if winner == "cancel":
            await JobService(session).cancel(queued["id"], ctx.student)
            assert await asyncio.wait_for(competing, timeout=5) == "lease_lost"
        else:
            submission = await session.get(Submission, queued["id"])
            await SubmissionService(session, sandbox=Runner(), lease=lease)._finalize(
                submission, VerdictResult("SAMPLE_PASS", 1, 1), triage(""),
                JudgeSuite("samples", [], "race-digest"),
            )
            assert (await asyncio.wait_for(competing, timeout=5)).status_code == 409
    async with ctx.sessions() as session:
        job = await session.get(JudgeJob, lease.job_id)
        submission = await session.get(Submission, queued["id"])
        if winner == "cancel":
            assert job.status == "cancelled" and submission.stage == "CANCELLED"
            assert submission.verdict is None
        else:
            assert job.status == "succeeded" and submission.stage == "READY"
            assert submission.verdict == "SAMPLE_PASS"


async def test_cancelling_a_running_worker_stops_execution_without_automatic_retry(jobs_context, monkeypatch):
    ctx = jobs_context
    monkeypatch.setattr(settings, "judge_job_lease_seconds", 5)
    entered = asyncio.Event()
    interrupted = asyncio.Event()

    class HangingRunner(Runner):
        async def compile(self, code):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                interrupted.set()
                raise

    monkeypatch.setattr("app.services.submission_service.build_runner", HangingRunner)
    queued = await enqueue(ctx)
    worker = asyncio.create_task(JudgeWorker(ctx.sessions).run_once())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        response = await ctx.client.post(f"/api/v1/submissions/{queued['id']}/cancel", headers=ctx.headers())
        assert response.status_code == 200
        await asyncio.wait_for(interrupted.wait(), timeout=5)
        assert await asyncio.wait_for(worker, timeout=5) is True
        async with ctx.sessions() as session:
            job = await session.get(JudgeJob, queued["job"]["id"])
            assert job.status == "cancelled" and job.attempts == 1
    finally:
        if not worker.done():
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker


async def test_inline_failure_messages_do_not_expose_internal_errors(jobs_context, monkeypatch):
    ctx = jobs_context

    class BrokenRunner(Runner):
        async def compile(self, code):
            raise SandboxError("private-internal-config-value")

    monkeypatch.setattr("app.services.submission_service.build_runner", BrokenRunner)
    response = await ctx.client.post("/api/v1/submissions", headers=ctx.headers(),
                                     json={"problem_pid": ctx.problem.pid, "code": "int main(){}"})
    assert response.status_code == 503
    assert "private-internal-config-value" not in response.text
    assert "暂时不可用" in response.text


@pytest.mark.parametrize("field, bad", [("code", "\ud800"), ("code", "x\x00y"),
                                       ("problem_pid", "\ud800"), ("problem_pid", "x\x00y")])
async def test_unstorable_submission_text_returns_safe_422(jobs_context, field, bad):
    import json

    ctx = jobs_context
    body = {"problem_pid": ctx.problem.pid, "code": "int main(){}", field: bad}
    response = await ctx.client.post("/api/v1/submissions", headers={**ctx.headers(), "Content-Type": "application/json"},
                                     content=json.dumps(body, ensure_ascii=True).encode())
    assert response.status_code == 422
    assert "Traceback" not in response.text
    async with ctx.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Submission)) == 0
        assert await session.scalar(select(func.count()).select_from(JudgeJob)) == 0


async def test_valid_unicode_submission_keeps_original_code(jobs_context):
    ctx = jobs_context
    source = "\ufeff// 合法中文与 emoji 🙂\nint main(){ return 0; }  \n"
    queued = await enqueue(ctx, source)
    assert queued["code"] == source


@pytest.mark.parametrize("reason", ["interrupted", "lease_expired"])
async def test_terminal_failure_never_promises_an_automatic_retry(jobs_context, monkeypatch, reason):
    ctx = jobs_context
    monkeypatch.setattr(settings, "judge_job_max_attempts", 1)
    queued = await enqueue(ctx)
    lease = await claim(ctx)
    async with ctx.sessions() as session:
        assert await JobService(session).fail(lease, reason) is True
    body = (await ctx.client.get(f"/api/v1/submissions/{queued['id']}", headers=ctx.headers())).json()
    assert body["job"]["status"] == "failed"
    assert "自动尝试上限" in body["job"]["last_error"]
    assert "稍后重新执行" not in body["job"]["last_error"]
    assert "恢复流程" not in body["job"]["last_error"]
