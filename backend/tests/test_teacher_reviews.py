"""真实 PostgreSQL HTTP 复核回归：归属、证据、版本、审计与机器结果保护。"""

import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from app.capabilities.sandbox import CompileResult
from app.core.security import create_access_token
from app.domain.models import Base, Diagnosis, DiagnosisReview, Evidence, Problem, Submission, User
from app.infra.db import get_session
from app.main import app
from app.services.review_service import ReviewService


async def _reviewable(context, user=None):
    submission = await context.submission(user=user)
    evidence = Evidence(submission_id=submission.id, type="run_trace", payload={
        "input": "1 2\n", "stdout": "4", "expected": "3", "status": "Accepted", "exit_code": 0,
    })
    context.session.add(evidence)
    await context.session.flush()
    diagnosis = await context.session.scalar(
        select(Diagnosis).where(Diagnosis.submission_id == submission.id)
    )
    diagnosis.status = "needs_human"
    diagnosis.narrative = "机器仅能确认输出不一致，具体原因待核对。"
    diagnosis.mechanism = [{"code": "M10", "confidence": 0.5,
                             "evidence_ids": [evidence.id], "explain": "可能为实现细节"}]
    await context.session.flush()
    return submission, diagnosis, evidence


def _payload(submission, evidence, **changes):
    return {
        "submission_id": submission.id, "expected_revision": 0, "decision": "confirm",
        "narrative": "已对照执行证据确认输出确实不一致，请先检查计算过程。",
        "evidence_ids": [evidence.id], **changes,
    }


async def test_teacher_confirms_with_replayable_audit_without_changing_verdict(api_context):
    ctx = api_context
    submission, diagnosis, evidence = await _reviewable(ctx)
    before_text = diagnosis.narrative
    result = await ctx.client.post("/api/v1/coach/reviews", headers=ctx.headers(ctx.users.teacher),
                                   json=_payload(submission, evidence))
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["diagnosis"]["status"] == "confirmed"
    assert data["diagnosis"]["review_revision"] == 1
    assert data["review"]["actor"] == {"id": ctx.users.teacher.id, "name": "teacher"}
    assert data["review"]["before"]["narrative"] == before_text
    assert data["review"]["after"]["narrative"] == data["review"]["narrative"]
    assert submission.verdict == "WA"
    detail = await ctx.client.get(f"/api/v1/submissions/{submission.id}", headers=ctx.headers())
    assert detail.json()["verdict"] == "WA"
    assert detail.json()["diagnosis"]["reviewer_id"] == ctx.users.teacher.id
    history = await ctx.client.get(f"/api/v1/submissions/{submission.id}/reviews", headers=ctx.headers())
    assert history.json()["current_revision"] == 1
    assert history.json()["items"][0]["created_at"]
    queue = await ctx.client.get("/api/v1/coach/queue", headers=ctx.headers(ctx.users.teacher))
    assert queue.json() == []


async def test_teacher_can_return_then_revise_without_overwriting_history(api_context):
    ctx = api_context
    submission, _, evidence = await _reviewable(ctx)
    response = await ctx.client.post("/api/v1/coach/reviews", headers=ctx.headers(ctx.users.teacher),
                                     json=_payload(submission, evidence, decision="return", evidence_ids=[],
                                                   narrative="现有证据不足以定因，请补充读入与计算过程。"))
    assert response.status_code == 200
    assert response.json()["diagnosis"]["status"] == "needs_human"
    response = await ctx.client.post("/api/v1/coach/reviews", headers=ctx.headers(ctx.users.teacher),
                                     json=_payload(submission, evidence, expected_revision=1, decision="revise",
                                                   mechanism=[{"code": "M10", "explain": "实际输出与预期相差一。",
                                                               "evidence_ids": [evidence.id]}]))
    assert response.status_code == 200, response.text
    assert response.json()["diagnosis"]["status"] == "confirmed"
    history = await ctx.client.get(f"/api/v1/submissions/{submission.id}/reviews", headers=ctx.headers())
    assert [item["decision"] for item in history.json()["items"]] == ["return", "revise"]
    assert history.json()["items"][1]["before"] == history.json()["items"][0]["after"]
    assert submission.verdict == "WA"


async def test_student_and_other_class_teacher_cannot_review_or_read_audit(api_context):
    ctx = api_context
    submission, _, evidence = await _reviewable(ctx)
    for user, status in [
        (ctx.users.student, 403), (ctx.users.other_teacher, 404), (ctx.users.blank_teacher, 404),
    ]:
        response = await ctx.client.post("/api/v1/coach/reviews", headers=ctx.headers(user),
                                         json=_payload(submission, evidence))
        assert response.status_code == status
    for user in (ctx.users.other, ctx.users.other_teacher, ctx.users.blank_teacher):
        response = await ctx.client.get(f"/api/v1/submissions/{submission.id}/reviews", headers=ctx.headers(user))
        assert response.status_code == 404
    response = await ctx.client.post("/api/v1/coach/reviews", headers=ctx.headers(ctx.users.admin),
                                     json=_payload(submission, evidence))
    assert response.status_code == 200


@pytest.mark.parametrize("changes", [
    {"narrative": ""}, {"narrative": "      "}, {"evidence_ids": []},
    {"expected_revision": -1}, {"decision": "accepted"}, {"verdict": "AC"},
    {"evidence_ids": [True]},
    {"mechanism": [{"code": "M99", "explain": "不存在的机制", "evidence_ids": [1]}]},
])
async def test_invalid_review_requests_are_rejected(api_context, changes):
    ctx = api_context
    submission, _, evidence = await _reviewable(ctx)
    response = await ctx.client.post("/api/v1/coach/reviews", headers=ctx.headers(ctx.users.teacher),
                                     json=_payload(submission, evidence, **changes))
    assert response.status_code == 422, response.text
    assert await ctx.session.scalar(select(func.count()).select_from(DiagnosisReview)) == 0
    assert submission.verdict == "WA"


async def test_foreign_or_nonexistent_evidence_cannot_support_review(api_context):
    ctx = api_context
    submission, _, evidence = await _reviewable(ctx)
    _, _, foreign_evidence = await _reviewable(ctx, ctx.users.other)
    for changes in [
        {"evidence_ids": [foreign_evidence.id]}, {"evidence_ids": [9_999_999_999]},
        {"decision": "revise", "mechanism": [{"code": "M10", "explain": "错误证据引用",
                                                "evidence_ids": [foreign_evidence.id]}]},
    ]:
        response = await ctx.client.post("/api/v1/coach/reviews", headers=ctx.headers(ctx.users.teacher),
                                         json=_payload(submission, evidence, **changes))
        assert response.status_code == 422


async def test_old_revision_cannot_overwrite_successful_review(api_context):
    ctx = api_context
    submission, _, evidence = await _reviewable(ctx)
    original = _payload(submission, evidence)
    first = await ctx.client.post("/api/v1/coach/reviews", headers=ctx.headers(ctx.users.teacher), json=original)
    assert first.status_code == 200
    stale = await ctx.client.post("/api/v1/coach/reviews", headers=ctx.headers(ctx.users.admin),
                                  json={**original, "narrative": "这是另一个编辑器里尚未刷新的旧版本审定。"})
    assert stale.status_code == 409
    assert await ctx.session.scalar(select(func.count()).select_from(DiagnosisReview)) == 1


async def test_automatic_rerun_increments_version_and_preserves_manual_review(api_context, monkeypatch):
    ctx = api_context
    submission, diagnosis, evidence = await _reviewable(ctx)
    rerun = await ctx.client.post(f"/api/v1/submissions/{submission.id}/diagnose", headers=ctx.headers())
    assert rerun.status_code == 200, rerun.text
    assert diagnosis.review_revision == 1
    stale = await ctx.client.post("/api/v1/coach/reviews", headers=ctx.headers(ctx.users.teacher),
                                  json=_payload(submission, evidence))
    assert stale.status_code == 409
    review = await ctx.client.post("/api/v1/coach/reviews", headers=ctx.headers(ctx.users.teacher),
                                   json=_payload(submission, evidence, expected_revision=1, decision="revise"))
    assert review.status_code == 200

    async def forbidden_model_call(**kwargs):
        raise AssertionError("人工审定之后不能重新调用诊断图")

    monkeypatch.setattr("app.api.v1.submissions.run_diagnosis", forbidden_model_call)
    repeated = await ctx.client.post(f"/api/v1/submissions/{submission.id}/diagnose", headers=ctx.headers())
    assert repeated.status_code == 409
    assert diagnosis.narrative == review.json()["review"]["narrative"]


async def test_database_rejects_audit_updates_and_deletes(api_context):
    ctx = api_context
    submission, _, evidence = await _reviewable(ctx)
    response = await ctx.client.post("/api/v1/coach/reviews", headers=ctx.headers(ctx.users.teacher),
                                     json=_payload(submission, evidence))
    audit_id = response.json()["review"]["id"]
    for statement in [
        update(DiagnosisReview).where(DiagnosisReview.id == audit_id).values(narrative="篡改历史记录"),
        delete(DiagnosisReview).where(DiagnosisReview.id == audit_id),
    ]:
        with pytest.raises(DBAPIError, match="append-only"):
            async with ctx.session.begin_nested():
                await ctx.session.execute(statement)
    audit = await ctx.session.get(DiagnosisReview, audit_id)
    assert audit.narrative == _payload(submission, evidence)["narrative"]


@pytest.fixture
async def concurrent_context(api_context):
    """独立 schema 提供已提交种子；每个 HTTP 请求使用独立 PG 连接。"""
    schema = "review_race_" + uuid4().hex
    engine = create_async_engine(
        os.environ["TEST_DATABASE_URL"], poolclass=NullPool,
        execution_options={"schema_translate_map": {None: schema}},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    previous_override = app.dependency_overrides.get(get_session)
    try:
        async with engine.begin() as connection:
            await connection.execute(CreateSchema(schema))
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as session:
            student = User(username="student", role="student", class_name="A", password_hash="unused")
            teacher = User(username="teacher", role="teacher", class_name="A", password_hash="unused")
            problem = Problem(pid="RACE", title="并发审定测试")
            session.add_all([student, teacher, problem])
            await session.flush()
            submission = Submission(problem_id=problem.id, student_id=student.id, stage="READY", verdict="WA")
            session.add(submission)
            await session.flush()
            evidence = Evidence(submission_id=submission.id, type="run_trace", payload={"stdout": "4"})
            session.add(evidence)
            session.add(Diagnosis(submission_id=submission.id, status="needs_human", narrative="等待教师核对。"))
            await session.commit()
            teacher_headers = {"Authorization": "Bearer " + create_access_token(teacher.id, teacher.role, teacher.username)}
            student_headers = {"Authorization": "Bearer " + create_access_token(student.id, student.role, student.username)}

        async def independent_session():
            async with sessions() as session:
                yield session

        app.dependency_overrides[get_session] = independent_session
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://race") as client:
            yield SimpleNamespace(
                client=client, sessions=sessions, submission=submission, evidence=evidence,
                teacher_headers=teacher_headers, student_headers=student_headers, problem=problem,
            )
    finally:
        if previous_override is not None:
            app.dependency_overrides[get_session] = previous_override
        else:
            app.dependency_overrides.pop(get_session, None)
        async with engine.begin() as connection:
            await connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        await engine.dispose()


async def test_simultaneous_review_writes_accept_exactly_one_version(concurrent_context, monkeypatch):
    ctx = concurrent_context
    both_entered = asyncio.Event()
    entered = 0
    original_create = ReviewService.create

    async def start_together(service, body):
        nonlocal entered
        entered += 1
        if entered == 2:
            both_entered.set()
        await asyncio.wait_for(both_entered.wait(), timeout=10)
        return await original_create(service, body)

    monkeypatch.setattr(ReviewService, "create", start_together)
    responses = await asyncio.gather(*[
        ctx.client.post("/api/v1/coach/reviews", headers=ctx.teacher_headers,
                        json=_payload(ctx.submission, ctx.evidence))
        for _ in range(2)
    ])
    assert sorted(response.status_code for response in responses) == [200, 409]
    async with ctx.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(DiagnosisReview)) == 1
        assert (await session.scalar(select(Diagnosis))).review_revision == 1


async def test_simultaneous_identical_submission_reuses_one_inflight_job(concurrent_context, monkeypatch):
    from app.services.submission_service import SubmissionService

    ctx = concurrent_context
    both_entered = asyncio.Event()
    compile_started = asyncio.Event()
    finish_compile = asyncio.Event()
    entered = 0
    compile_count = 0
    original_create = SubmissionService.create

    async def start_together(service, *args, **kwargs):
        nonlocal entered
        entered += 1
        if entered == 2:
            both_entered.set()
        await asyncio.wait_for(both_entered.wait(), timeout=10)
        return await original_create(service, *args, **kwargs)

    class BlockedRunner:
        async def compile(self, code):
            nonlocal compile_count
            compile_count += 1
            compile_started.set()
            await asyncio.wait_for(finish_compile.wait(), timeout=10)
            return CompileResult(ok=True, stderr="", artifact_id="race-artifact")

        async def cleanup(self, artifact_id):
            pass

    monkeypatch.setattr(SubmissionService, "create", start_together)
    monkeypatch.setattr("app.services.submission_service.build_runner", BlockedRunner)
    payload = {"problem_pid": ctx.problem.pid, "code": "int main(){return 0;}",
               "parent_submission_id": ctx.submission.id}
    tasks = [asyncio.create_task(ctx.client.post("/api/v1/submissions", headers=ctx.student_headers,
                                                 json=payload)) for _ in range(2)]
    try:
        await asyncio.wait_for(compile_started.wait(), timeout=10)
        done, _ = await asyncio.wait(tasks, timeout=10, return_when=asyncio.FIRST_COMPLETED)
        assert done, "重复请求应在执行器结束前返回现有任务"
        duplicate = await next(iter(done))
        assert duplicate.status_code == 409, duplicate.text
        child_id = int(duplicate.headers["Location"].rsplit("/", 1)[1])
    finally:
        finish_compile.set()
        responses = await asyncio.gather(*tasks)
    assert sorted(response.status_code for response in responses) == [200, 409]
    success = next(response for response in responses if response.status_code == 200)
    assert success.json()["id"] == child_id
    assert success.json()["parent_submission_id"] == ctx.submission.id
    assert compile_count == 1
    async with ctx.sessions() as session:
        count = await session.scalar(select(func.count()).select_from(Submission).where(
            Submission.parent_submission_id == ctx.submission.id
        ))
        assert count == 1
