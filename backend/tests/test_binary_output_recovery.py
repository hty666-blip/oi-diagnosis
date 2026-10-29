"""真实 PostgreSQL 回归：二进制输出可展示，原始判定与失败恢复保持可靠。"""

import hashlib
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from app.capabilities.docker_runner import DockerRunner
from app.capabilities.sandbox import CommandResult, CompileResult
from app.capabilities.test_suite import JudgeSuite
from app.core.config import settings
from app.core.security import create_access_token
from app.domain.models import Base, Diagnosis, Evidence, JudgeJob, Problem, Submission, User
from app.infra.db import get_session
from app.main import app
from app.services.job_service import JobService, LeaseLost
from app.services.submission_service import SubmissionService
from app.services.tutor_service import TutorService
from app.worker import JudgeWorker

CODE = "int main(){return 0;}"
PRIVATE_ERROR = "private-output-recovery-detail"


@pytest.fixture
async def output_context(api_context):
    """工作器需要独立连接与真实提交，单测结束删除本测试独占 schema。"""
    schema = "binary_output_" + uuid4().hex
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
            student = User(username="binary_student", role="student", password_hash="unused")
            problem = Problem(pid="BINARY-TEST", title="输出存储测试", samples=[])
            session.add_all([student, problem])
            await session.commit()

        def headers():
            return {"Authorization": "Bearer " + create_access_token(
                student.id, student.role, student.username
            )}

        async def independent_session():
            async with sessions() as session:
                yield session

        app.dependency_overrides[get_session] = independent_session
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://binary-output"
        ) as client:
            yield SimpleNamespace(sessions=sessions, client=client, headers=headers,
                                  student=student, problem=problem)
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_session, None)
        else:
            app.dependency_overrides[get_session] = previous
        async with engine.begin() as connection:
            await connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        await engine.dispose()


class OutputRunner:
    def __init__(self, stdout="1\n", *, stderr="", compile_stderr=""):
        self.stdout = stdout
        self.stderr = stderr
        self.compile_stderr = compile_stderr
        self.cleaned = False
        self.closed = False

    async def compile(self, code):
        return CompileResult(ok=True, stderr=self.compile_stderr, artifact_id="memory")

    async def run(self, artifact_id, **kwargs):
        return CommandResult(status="Accepted", exit_code=0, time_ns=1, memory_bytes=1,
                             stdout=self.stdout, stderr=self.stderr)

    async def cleanup(self, artifact_id):
        self.cleaned = True

    async def close(self):
        self.closed = True


def _use_suite(monkeypatch, *, expected="1\n", stdin="2\n"):
    cases = [{"input": stdin, "output": expected}]
    digest = hashlib.sha256(json.dumps(cases).encode()).hexdigest()
    suite = JudgeSuite("package", cases, digest)
    monkeypatch.setattr("app.services.submission_service.load_suite", lambda _: suite)


async def _post(ctx, mode, *, code=CODE):
    headers = ctx.headers()
    if mode == "background":
        headers["Prefer"] = "respond-async"
    return await ctx.client.post("/api/v1/submissions", headers=headers,
                                 json={"problem_pid": ctx.problem.pid, "code": code})


async def _complete(ctx, mode):
    response = await _post(ctx, mode)
    assert response.status_code == (202 if mode == "background" else 200), response.text
    submission_id = response.json()["id"]
    if mode == "background":
        assert await JudgeWorker(ctx.sessions).run_once() is True
    detail = await ctx.client.get(f"/api/v1/submissions/{submission_id}", headers=ctx.headers())
    assert detail.status_code == 200, detail.text
    return detail.json()


async def _evidence(ctx, submission_id):
    response = await ctx.client.get(
        f"/api/v1/submissions/{submission_id}/evidence", headers=ctx.headers()
    )
    assert response.status_code == 200, response.text
    return {row["type"]: row for row in response.json()}


@pytest.mark.parametrize("mode", ["inline", "background"])
@pytest.mark.parametrize("raw_match", [False, True], ids=["literal-is-different", "raw-nul-matches"])
async def test_nul_output_is_stored_without_changing_raw_judgement(
    output_context, monkeypatch, mode, raw_match,
):
    ctx = output_context
    # 两种期望展示后相同；只有原始 NUL 才应与实际 NUL 相等。
    _use_suite(monkeypatch, expected="\x00" if raw_match else "\\u0000", stdin="in\x00put")
    runner = OutputRunner("\x00", stderr="run\x00warning", compile_stderr="compile\x00warning")
    monkeypatch.setattr("app.services.submission_service.build_runner", lambda: runner)
    result = await _complete(ctx, mode)
    assert result["stage"] == "READY"
    assert result["verdict"] == ("AC" if raw_match else "WA")
    assert result["tests_passed"]["passed"] == int(raw_match)
    if mode == "background":
        assert result["job"]["status"] == "succeeded"
    rows = await _evidence(ctx, result["id"])
    trace = rows["run_trace"]["payload"]
    assert trace["stdout"] == trace["expected"] == "\\u0000"
    assert trace["matched"] is raw_match
    assert trace["input"] == "in\\u0000put"
    assert trace["stderr"] == "run\\u0000warning"
    assert set(trace["escaped_fields"]) == {
        "/input", "/stdout", "/stderr", *(["/expected"] if raw_match else []),
    }
    assert rows["run_trace"]["reproducible"] is False
    compiler = rows["compile_log"]["payload"]
    assert compiler["stderr"] == "compile\\u0000warning"
    assert compiler["escaped_fields"] == ["/stderr"]
    assert runner.cleaned and runner.closed
    async with ctx.sessions() as session:
        persisted = await session.get(Evidence, rows["run_trace"]["id"])
        assert persisted.payload == trace
        diagnosis = await session.scalar(select(Diagnosis).where(
            Diagnosis.submission_id == result["id"]
        ))
        view = await TutorService(session, ctx.student)._diagnosis_view(diagnosis, result["id"])
        assert view.counterexample == "", "转义后的输入不能冒充可执行的原始反例"


@pytest.mark.parametrize("mode", ["inline", "background"])
async def test_normal_unicode_and_literal_escape_text_remain_unchanged(output_context, monkeypatch, mode):
    ctx = output_context
    output = "中文输出 😀 \\u0000\n"
    stdin = "正常输入 😀\n"
    _use_suite(monkeypatch, expected=output, stdin=stdin)
    runner = OutputRunner(output, stderr="正常运行提示\n", compile_stderr="普通警告\n")
    monkeypatch.setattr("app.services.submission_service.build_runner", lambda: runner)
    result = await _complete(ctx, mode)
    assert result["verdict"] == "AC"
    rows = await _evidence(ctx, result["id"])
    trace = rows["run_trace"]["payload"]
    assert trace["stdout"] == trace["expected"] == output
    assert trace["input"] == stdin and trace["stderr"] == "正常运行提示\n"
    assert trace["matched"] is True
    assert "escaped_fields" not in trace
    assert rows["run_trace"]["reproducible"] is True
    assert rows["compile_log"]["payload"] == {"ok": True, "stderr": "普通警告\n"}


@pytest.mark.parametrize("mode", ["inline", "background"])
@pytest.mark.parametrize("failure", ["runner", "failed-flush"])
async def test_unexpected_failure_rolls_back_and_allows_a_new_submission(
    output_context, monkeypatch, mode, failure,
):
    ctx = output_context
    _use_suite(monkeypatch)
    monkeypatch.setattr(settings, "judge_job_max_attempts", 1)
    runner = OutputRunner()
    original_run = runner.run

    async def broken_run(*args, **kwargs):
        raise RuntimeError(PRIVATE_ERROR)

    original_add = SubmissionService._add_evidence

    async def broken_flush(service, submission_id, kind, payload, **kwargs):
        if kind == "run_trace":
            # 真实外键错误让 AsyncSession 进入 PendingRollbackError 状态。
            service.session.add(Evidence(submission_id=-1, type="run_trace",
                                         payload={"private": PRIVATE_ERROR}))
        return await original_add(service, submission_id, kind, payload, **kwargs)

    if failure == "runner":
        monkeypatch.setattr(runner, "run", broken_run)
    else:
        monkeypatch.setattr(SubmissionService, "_add_evidence", broken_flush)
    monkeypatch.setattr("app.services.submission_service.build_runner", lambda: runner)
    response = await _post(ctx, mode)
    if mode == "inline":
        assert response.status_code == 503, response.text
        assert response.json() == {"detail": "判题服务暂时不可用，请稍后重试"}
    else:
        assert response.status_code == 202, response.text
        assert await JudgeWorker(ctx.sessions).run_once() is True
    assert PRIVATE_ERROR not in response.text
    async with ctx.sessions() as session:
        failed = await session.scalar(select(Submission))
        failed_id = failed.id
        assert failed.stage == "SANDBOX_ERROR" and failed.verdict is None
        assert failed.tests_passed is None
        assert await session.scalar(select(func.count()).select_from(Evidence).where(
            Evidence.type == "run_trace"
        )) == 0
        if mode == "background":
            job = await session.scalar(select(JudgeJob))
            assert job.status == "failed" and job.owner_token is None
            assert job.lease_expires_at is None and PRIVATE_ERROR not in job.last_error
    assert runner.cleaned and runner.closed

    monkeypatch.setattr(SubmissionService, "_add_evidence", original_add)
    monkeypatch.setattr(runner, "run", original_run)
    recovered = await _complete(ctx, mode)
    assert recovered["id"] != failed_id and recovered["verdict"] == "AC"
    if mode == "background":
        assert recovered["job"]["status"] == "succeeded"


@pytest.mark.parametrize("mode", ["inline", "background"])
async def test_cleanup_exception_preserves_completed_result_and_closes_runner(output_context, monkeypatch, mode):
    ctx = output_context
    _use_suite(monkeypatch)
    runner = OutputRunner()

    async def broken_cleanup(artifact_id):
        runner.cleaned = True
        raise RuntimeError(PRIVATE_ERROR)

    monkeypatch.setattr(runner, "cleanup", broken_cleanup)
    monkeypatch.setattr("app.services.submission_service.build_runner", lambda: runner)
    result = await _complete(ctx, mode)
    assert result["stage"] == "READY" and result["verdict"] == "AC"
    assert runner.cleaned and runner.closed
    if mode == "background":
        assert result["job"]["status"] == "succeeded"
        assert result["job"]["last_error"] is None


async def test_cancelled_owner_cannot_save_even_sanitized_binary_evidence(output_context, monkeypatch):
    ctx = output_context
    _use_suite(monkeypatch)
    response = await _post(ctx, "background")
    assert response.status_code == 202, response.text
    queued = response.json()
    async with ctx.sessions() as session:
        lease = await JobService(session).claim()
    cancelled = await ctx.client.post(
        f"/api/v1/submissions/{queued['id']}/cancel", headers=ctx.headers()
    )
    assert cancelled.status_code == 200, cancelled.text
    async with ctx.sessions() as session:
        service = SubmissionService(session, sandbox=OutputRunner(), lease=lease)
        with pytest.raises(LeaseLost):
            await service._add_evidence(queued["id"], "run_trace", {
                "stdout": "\x00", "input": "\x00", "expected": "\\u0000",
            })
        await session.rollback()
    async with ctx.sessions() as session:
        job = await session.get(JudgeJob, lease.job_id)
        submission = await session.get(Submission, queued["id"])
        assert job.status == "cancelled" and submission.stage == "CANCELLED"
        assert job.owner_token is None and job.lease_expires_at is None
        assert submission.verdict is None
        assert await session.scalar(select(func.count()).select_from(Evidence)) == 0


@pytest.mark.skipif(os.getenv("RUN_DOCKER_TESTS") != "1", reason="需要显式启用 Docker 判题集成")
async def test_real_cpp_nul_output_is_a_saved_wrong_answer(output_context, monkeypatch):
    ctx = output_context
    _use_suite(monkeypatch, expected="\\u0000", stdin="")
    monkeypatch.setattr("app.services.submission_service.build_runner", DockerRunner)
    code = "#include <iostream>\nint main(){std::cout.put(0);}\n"
    response = await _post(ctx, "inline", code=code)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["stage"] == "READY" and result["verdict"] == "WA"
    trace = (await _evidence(ctx, result["id"]))["run_trace"]["payload"]
    assert trace["stdout"] == trace["expected"] == "\\u0000"
    assert trace["matched"] is False and trace["escaped_fields"] == ["/stdout"]
