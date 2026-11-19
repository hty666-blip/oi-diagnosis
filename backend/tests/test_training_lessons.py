"""训练学情与讲评：事实口径、归属、版本、模型失败与发布边界。"""
import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_trainings import create

from app.domain.models import (
    Diagnosis,
    Evidence,
    JudgeJob,
    Problem,
    Training,
    TrainingLessonPublication,
    TrainingLessonRevision,
    TrainingMember,
    TrainingProblem,
    User,
)
from app.services.training_lesson_service import LessonSave, TrainingLessonService


async def lesson(ctx, tid, user=None):
    r = await ctx.client.get(f"/api/v1/trainings/{tid}/lesson", headers=ctx.headers(user or ctx.users.teacher))
    assert r.status_code == 200, r.text
    return r.json()


async def save(ctx, tid, text="教练讲解：逐句核对循环与累计更新。", revision=0, digest=None):
    digest = digest or (await lesson(ctx, tid))["analysis"]["basis_digest"]
    return await ctx.client.post(f"/api/v1/trainings/{tid}/lesson/draft", headers={**ctx.headers(ctx.users.teacher), "Content-Type": "application/json"},
        content=json.dumps({"expected_revision": revision, "content": text, "basis_digest": digest}))


async def generate(ctx, tid, revision=0):
    return await ctx.client.post(f"/api/v1/trainings/{tid}/lesson/generate", headers=ctx.headers(ctx.users.teacher),
                                 json={"expected_revision": revision})


async def test_analytics_latest_only_distinguishes_repair_sample_fault_and_unclassified(api_context):
    ctx = api_context
    task = await create(ctx, publish=True)
    tid = task["id"]
    await ctx.submission(training_id=tid)
    fixed = await ctx.submission(training_id=tid, verdict="AC", tests_passed={"passed": 1, "total": 1, "scope": "package"})
    await ctx.submission(user=ctx.users.peer, training_id=tid, verdict="AC", tests_passed={"passed": 1, "total": 1, "scope": "samples"})
    await ctx.submission()  # 自由练习不作为案例或错因。
    analysis = (await lesson(ctx, tid))["analysis"]
    assert analysis["problems"][0]["counts"] == {"AC": 1, "SAMPLE_PASS": 1}
    assert analysis["problems"][0]["repaired"] == 1 and analysis["problems"][0]["unclassified"] == 0
    assert len(analysis["examples"]) == 2
    ctx.session.add(JudgeJob(submission_id=fixed.id, status="failed"))
    await ctx.session.flush()
    analysis = (await lesson(ctx, tid))["analysis"]
    assert analysis["problems"][0]["counts"] == {"failed": 1, "SAMPLE_PASS": 1}
    assert analysis["summary"]["completed"] == 0 and analysis["issues"] == []
    assert all(example["submission_id"] != fixed.id for example in analysis["examples"])


async def test_mechanisms_require_real_evidence_and_count_students_once_with_separate_review_sources(api_context):
    ctx = api_context
    tid = (await create(ctx, publish=True))["id"]
    first = await ctx.submission(training_id=tid)
    second = await ctx.submission(user=ctx.users.peer, training_id=tid)
    for sub in (first, second):
        evidence = Evidence(submission_id=sub.id, type="run_trace", payload={"stdout": "2", "expected": "-4"})
        ctx.session.add(evidence)
        await ctx.session.flush()
        diag = (await ctx.session.scalars(select(Diagnosis).where(Diagnosis.submission_id == sub.id))).one()
        diag.mechanism = [{"code": "M10", "evidence_ids": [evidence.id]}] * 2 + [{"code": "M01", "evidence_ids": [999999]}]
        if sub == first:
            r = await ctx.client.post("/api/v1/coach/reviews", headers=ctx.headers(ctx.users.teacher), json={
                "submission_id": sub.id, "expected_revision": 0, "decision": "confirm",
                "narrative": "根据失败用例核对覆盖赋值。", "evidence_ids": [evidence.id]})
            assert r.status_code == 422  # 不能顺带确认虚假的机制引用。
            diag.mechanism = [{"code": "M10", "evidence_ids": [evidence.id]}]
            await ctx.session.flush()
            r = await ctx.client.post("/api/v1/coach/reviews", headers=ctx.headers(ctx.users.teacher), json={
                "submission_id": sub.id, "expected_revision": 0, "decision": "confirm",
                "narrative": "根据失败用例核对覆盖赋值。", "evidence_ids": [evidence.id]})
            assert r.status_code == 200, r.text
    await ctx.session.flush()
    data = (await lesson(ctx, tid))["analysis"]
    assert {(g["source"], g["students"], g["code"]) for g in data["issues"]} == {("teacher", 1, "M10"), ("automatic", 1, "M10")}


async def test_students_never_receive_drafts_analytics_or_other_students_cases(api_context):
    ctx = api_context
    tid = (await create(ctx, publish=True))["id"]
    assert (await save(ctx, tid)).status_code == 200
    assert await lesson(ctx, tid, ctx.users.student) == {"published": None}
    assert (await ctx.client.get(f"/api/v1/trainings/{tid}/analysis", headers=ctx.headers())).status_code == 403
    for endpoint in ("generate", "draft", "publish"):
        r = await ctx.client.post(f"/api/v1/trainings/{tid}/lesson/{endpoint}", headers=ctx.headers(), json={"expected_revision": 0, "content": "some text", "basis_digest": "a" * 64})
        assert r.status_code == 403
    for actor in (ctx.users.other, ctx.users.other_teacher, ctx.users.blank_teacher):
        r = await ctx.client.get(f"/api/v1/trainings/{tid}/lesson", headers=ctx.headers(actor))
        assert r.status_code == 404


async def test_edit_draft_does_not_change_published_lesson_and_publish_is_idempotent(api_context):
    ctx = api_context
    tid = (await create(ctx, publish=True))["id"]
    r = await save(ctx, tid)
    assert r.status_code == 200 and r.json()["draft"]["revision"] == 1
    for _ in range(2):
        r = await ctx.client.post(f"/api/v1/trainings/{tid}/lesson/publish", headers=ctx.headers(ctx.users.teacher), json={"expected_revision": 1})
        assert r.status_code == 200
    old = await lesson(ctx, tid, ctx.users.student)
    assert old["published"]["revision"] == 1 and set(old) == {"published"}
    assert (await save(ctx, tid, text="第二版讲评：请看累计更新的局部代码。", revision=1)).status_code == 200
    assert (await lesson(ctx, tid, ctx.users.student)) == old
    assert len((await lesson(ctx, tid))["history"]) == 2
    assert len((await ctx.session.scalars(select(TrainingLessonPublication))).all()) == 1


async def test_stale_data_rejects_publish_and_stale_write_preserves_old_revision(api_context):
    ctx = api_context
    tid = (await create(ctx, publish=True))["id"]
    initial = (await lesson(ctx, tid))["analysis"]["basis_digest"]
    assert initial == (await lesson(ctx, tid))["analysis"]["basis_digest"]
    assert (await save(ctx, tid, digest=initial)).status_code == 200
    await ctx.submission(training_id=tid)
    current = await lesson(ctx, tid)
    assert current["draft"]["stale"] is True
    r = await ctx.client.post(f"/api/v1/trainings/{tid}/lesson/publish", headers=ctx.headers(ctx.users.teacher), json={"expected_revision": 1})
    assert r.status_code == 409
    assert (await save(ctx, tid, revision=1, digest=initial)).status_code == 409
    assert (await save(ctx, tid, revision=0)).status_code == 409
    assert (await save(ctx, tid, revision=1)).status_code == 200
    assert (await lesson(ctx, tid))["draft"]["stale"] is False


async def test_generation_without_model_is_explicit_facts_draft(api_context):
    ctx = api_context
    tid = (await create(ctx, publish=True))["id"]
    r = await generate(ctx, tid)
    assert r.status_code == 200, r.text
    assert r.json()["draft"]["source"] == "facts"
    assert "已参与 0 人" in r.json()["draft"]["content"]
    assert (await lesson(ctx, tid, ctx.users.student))["published"] is None


@pytest.mark.parametrize("invalid", ["outside_problem", "outside_submission", "outside_evidence", "wrong_type", "exception", "empty"])
async def test_invalid_model_citations_or_model_failure_fall_back_without_publishing(api_context, monkeypatch, invalid):
    ctx = api_context
    tid = (await create(ctx, publish=True))["id"]
    sub = await ctx.submission(training_id=tid, code="int sum=0; sum=x;")
    class Model:
        available = True

        async def chat_json(self, *args, **kwargs):
            if invalid == "exception":
                raise RuntimeError("unavailable")
            if invalid == "empty":
                return None
            section = {"pid": ctx.problem.pid, "title": "覆盖赋值", "explanation": "sum=x 每次只保存本次读入值，应当对照题意解释累计更新。", "submission_ids": [sub.id], "evidence_ids": []}
            if invalid == "outside_problem":
                section["pid"] = "OTHER"
            if invalid == "outside_submission":
                section["submission_ids"] = [999999]
            if invalid == "outside_evidence":
                section["evidence_ids"] = [999999]
            if invalid == "wrong_type":
                section["submission_ids"] = [True]
            return {"sections": [section], "questions": []}
    monkeypatch.setattr("app.services.training_lesson_service.LLMClient", Model)
    monkeypatch.setattr("app.core.config.settings.llm_enabled", True)
    r = await generate(ctx, tid)
    assert r.status_code == 200 and r.json()["draft"]["source"] == "facts"
    assert r.json()["published"] is None


async def test_model_receives_code_problem_and_evidence_without_identity_fields(api_context, monkeypatch):
    ctx = api_context
    ctx.users.student.display_name = "不应外发的姓名甲"
    ctx.problem.statement_md = "读取整数并求和。"
    tid = (await create(ctx, publish=True))["id"]
    sub = await ctx.submission(training_id=tid, code="int sum=0; sum=x;")
    evidence = Evidence(submission_id=sub.id, type="run_trace", payload={"stdout": "2", "expected": "-4"})
    ctx.session.add(evidence)
    await ctx.session.flush()
    captured = []
    class Model:
        available = True

        async def chat_json(self, system, prompt, **kwargs):
            captured.append(prompt)
            return {"sections": [{"pid": ctx.problem.pid, "title": "累计更新", "explanation": f"提交 {sub.id} 的 `sum=x` 会覆盖旧值；证据 {evidence.id} 对应实际与预期差异，逐步说明旧值与新值。", "submission_ids": [sub.id], "evidence_ids": [evidence.id]}], "questions": ["如果旧值为 -5，新值为 -1，累加后是多少？"]}
    monkeypatch.setattr("app.services.training_lesson_service.LLMClient", Model)
    monkeypatch.setattr("app.core.config.settings.llm_enabled", True)
    r = await generate(ctx, tid)
    assert r.status_code == 200, r.text
    assert r.json()["draft"]["source"] == "ai" and "sum=x" in r.json()["draft"]["content"]
    assert "不应外发的姓名甲" not in captured[0] and ctx.users.student.username not in captured[0]
    assert "读取整数并求和" in captured[0] and "-4" in captured[0]
    assert "匿名案例" in r.json()["draft"]["content"] and f"提交 {sub.id}" not in r.json()["draft"]["content"]


async def test_model_generation_cannot_overwrite_new_training_results(api_context, monkeypatch):
    ctx = api_context
    tid = (await create(ctx, publish=True))["id"]
    sub = await ctx.submission(training_id=tid)
    assert (await save(ctx, tid)).status_code == 200
    class Model:
        available = True

        async def chat_json(self, *args, **kwargs):
            await ctx.submission(training_id=tid, code_hash="newer")
            return {"sections": [{"pid": ctx.problem.pid, "title": "讲解", "explanation": "这是针对原来提交记录的具体语句讲解。", "submission_ids": [sub.id], "evidence_ids": []}], "questions": []}
    monkeypatch.setattr("app.services.training_lesson_service.LLMClient", Model)
    monkeypatch.setattr("app.core.config.settings.llm_enabled", True)
    assert (await generate(ctx, tid, revision=1)).status_code == 409
    assert (await lesson(ctx, tid))["draft"]["revision"] == 1


@pytest.mark.parametrize("content", [" ", "\x00", "\ud800"])
async def test_invalid_lecture_text_is_rejected(api_context, content):
    ctx = api_context
    tid = (await create(ctx, publish=True))["id"]
    assert (await save(ctx, tid, text=content)).status_code == 422


async def test_concurrent_lesson_saves_accept_only_one_revision(api_context, isolated_database):
    sessions = async_sessionmaker(isolated_database, expire_on_commit=False)
    async with sessions() as session:
        user = User(username="lesson_teacher", role="teacher", class_name="A", password_hash="unused")
        student = User(username="lesson_student", role="student", class_name="A", password_hash="unused")
        problem = Problem(pid="LESSON-TEST", title="测试", samples=[])
        session.add_all([user, student, problem])
        await session.flush()
        training = Training(teacher_id=user.id, class_name="A", title="并发讲评", objective="", description="", status="published", due_at=datetime.now(UTC) + timedelta(days=1))
        session.add(training)
        await session.flush()
        session.add_all([TrainingProblem(training_id=training.id, problem_id=problem.id, position=0), TrainingMember(training_id=training.id, student_id=student.id)])
        await session.commit()
        tid, uid = training.id, user.id
        data = await TrainingLessonService(session, user).get(tid)
        digest = data["analysis"]["basis_digest"]
    async def write():
        async with sessions() as session:
            user = await session.get(User, uid)
            try:
                await TrainingLessonService(session, user).save(tid, LessonSave(expected_revision=0, content="并发编辑的讲评内容。", basis_digest=digest))
                return 200
            except HTTPException as exc:
                return exc.status_code
    assert sorted(await asyncio.gather(write(), write())) == [200, 409]
    async with sessions() as session:
        assert len((await session.scalars(select(TrainingLessonRevision))).all()) == 1
