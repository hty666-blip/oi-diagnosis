"""个别指导、定向补练与档案归属，所有结果来自实际保存的提交状态。"""
import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_trainings import create, payload

from app.domain.models import JudgeJob, Problem, Training, TrainingFeedback, TrainingMember, User
from app.services.student_followup_service import FeedbackCreate, create_feedback


async def feedback(ctx, tid, *, student=None, actor=None, content="请逐轮检查累计值与输入值的变化。", source=None, request=None):
    return await ctx.client.post(f"/api/v1/trainings/{tid}/students/{(student or ctx.users.student).id}/feedback",
        headers={**ctx.headers(actor or ctx.users.teacher), "Content-Type": "application/json"},
        content=json.dumps({"content": content, "submission_id": source, "request_id": request or str(uuid4())}))


async def history(ctx, tid, student=None, actor=None):
    return await ctx.client.get(f"/api/v1/trainings/{tid}/students/{(student or ctx.users.student).id}/feedback", headers=ctx.headers(actor or ctx.users.teacher))


async def profile(ctx, student=None, actor=None):
    return await ctx.client.get(f"/api/v1/students/{(student or ctx.users.student).id}/profile", headers=ctx.headers(actor or ctx.users.teacher))


async def test_targeted_remedial_task_reaches_only_selected_student_and_preserves_original(api_context):
    ctx = api_context
    original = await create(ctx, publish=True)
    await ctx.submission(training_id=original["id"])
    child = await create(ctx, publish=True, title="定向补练", source_training_id=original["id"], student_ids=[ctx.users.student.id])
    assert child["student_ids"] == [ctx.users.student.id] and child["summary"]["students"] == 1
    assert (await ctx.client.get(f"/api/v1/trainings/{child['id']}", headers=ctx.headers(ctx.users.peer))).status_code == 404
    owned = (await ctx.client.get(f"/api/v1/trainings/{child['id']}", headers=ctx.headers())).json()
    assert "student_ids" not in owned and owned["source_training_id"] == original["id"]
    assert len((await ctx.client.get("/api/v1/trainings", headers=ctx.headers(ctx.users.peer))).json()) == 1
    await ctx.submission(training_id=child["id"], verdict="AC", tests_passed={"passed": 3, "total": 3, "scope": "package"})
    data = (await profile(ctx)).json()
    assert data["summary"] == {"trainings": 2, "completed": 1, "remedial": 1}
    assert data["items"][0]["complete"] is True and data["items"][1]["complete"] is False


@pytest.mark.parametrize("case", ["empty", "duplicate", "other_class", "teacher", "not_original_member", "missing_audience", "draft_source"])
async def test_invalid_target_or_remedial_source_is_rejected(api_context, case):
    ctx = api_context
    source = await create(ctx, publish=case != "draft_source")
    body = payload(ctx, source_training_id=source["id"], student_ids=[ctx.users.student.id])
    if case == "empty":
        body["student_ids"] = []
    if case == "duplicate":
        body["student_ids"] *= 2
    if case == "other_class":
        body["student_ids"] = [ctx.users.other.id]
    if case == "teacher":
        body["student_ids"] = [ctx.users.teacher.id]
    if case == "missing_audience":
        body["student_ids"] = None
    if case == "not_original_member":
        newcomer = User(username="late_joiner", role="student", class_name="A", password_hash="unused")
        ctx.session.add(newcomer)
        await ctx.session.flush()
        body["student_ids"] = [newcomer.id]
    r = await ctx.client.post("/api/v1/trainings", headers=ctx.headers(ctx.users.teacher), json=body)
    assert r.status_code == 422, r.text


async def test_publish_checks_changed_target_class_and_keeps_draft(api_context):
    ctx = api_context
    draft = await create(ctx, student_ids=[ctx.users.student.id])
    ctx.users.student.class_name = "B"
    await ctx.session.flush()
    r = await ctx.client.post(f"/api/v1/trainings/{draft['id']}/publish", headers=ctx.headers(ctx.users.teacher))
    assert r.status_code == 409
    row = await ctx.session.get(Training, draft["id"])
    assert row.status == "draft"


async def test_feedback_request_retry_is_idempotent_but_changed_payload_conflicts(api_context):
    ctx = api_context
    tid = (await create(ctx, publish=True))["id"]
    token = str(uuid4())
    first = await feedback(ctx, tid, request=token)
    again = await feedback(ctx, tid, request=token)
    assert first.status_code == again.status_code == 200
    assert len(again.json()["items"]) == 1 and first.json()["items"][0]["id"] == again.json()["items"][0]["id"]
    assert (await feedback(ctx, tid, request=token, content="内容已经变化，应作为新的反馈发送。")).status_code == 409
    assert (await feedback(ctx, tid)).status_code == 200
    assert len((await history(ctx, tid)).json()["items"]) == 2


async def test_feedback_permissions_and_source_submission_scope(api_context):
    ctx = api_context
    tid = (await create(ctx, publish=True))["id"]
    own = await ctx.submission(training_id=tid)
    peer = await ctx.submission(user=ctx.users.peer, training_id=tid)
    free = await ctx.submission()
    assert (await feedback(ctx, tid, actor=ctx.users.student)).status_code == 403
    assert (await feedback(ctx, tid, actor=ctx.users.other_teacher)).status_code == 404
    assert (await feedback(ctx, tid, student=ctx.users.other)).status_code == 404
    for source in (peer.id, free.id, 999999):
        assert (await feedback(ctx, tid, source=source)).status_code == 422
    assert (await feedback(ctx, tid, source=own.id)).status_code == 200
    assert (await history(ctx, tid, actor=ctx.users.peer)).status_code == 404
    assert (await history(ctx, tid, actor=ctx.users.student)).status_code == 200
    assert (await history(ctx, tid, actor=ctx.users.other_teacher)).status_code == 404


async def test_read_marker_is_explicit_student_only_idempotent_and_not_judgement(api_context):
    ctx = api_context
    tid = (await create(ctx, publish=True))["id"]
    sub = await ctx.submission(training_id=tid)
    created = (await feedback(ctx, tid, source=sub.id)).json()["items"][0]
    assert (await history(ctx, tid, actor=ctx.users.student)).json()["items"][0]["read_at"] is None
    url = f"/api/v1/trainings/{tid}/feedback/{created['id']}/read"
    assert (await ctx.client.post(url, headers=ctx.headers(ctx.users.teacher))).status_code == 403
    assert (await ctx.client.post(url, headers=ctx.headers(ctx.users.peer))).status_code == 404
    first = await ctx.client.post(url, headers=ctx.headers())
    again = await ctx.client.post(url, headers=ctx.headers())
    assert first.status_code == again.status_code == 200
    assert first.json()["items"][0]["read_at"] == again.json()["items"][0]["read_at"]
    assert sub.verdict == "WA" and first.json()["items"][0]["followup"]["passed"] is False


async def test_followup_requires_new_submission_and_real_package_pass(api_context):
    ctx = api_context
    tid = (await create(ctx, publish=True))["id"]
    pending = await ctx.submission(training_id=tid, stage="QUEUED", verdict=None)
    assert (await feedback(ctx, tid, source=pending.id)).status_code == 200
    pending.stage = "READY"
    pending.verdict = "AC"
    pending.tests_passed = {"passed": 1, "total": 1, "scope": "package"}
    await ctx.session.flush()
    assert (await history(ctx, tid)).json()["items"][0]["followup"]["has_new_submission"] is False
    sample = await ctx.submission(training_id=tid, verdict="AC", tests_passed={"passed": 1, "total": 1, "scope": "samples"})
    follow = (await history(ctx, tid)).json()["items"][0]["followup"]
    assert follow == {"has_new_submission": True, "passed": False, "latest_submission_id": sample.id}
    fixed = await ctx.submission(training_id=tid, verdict="AC", tests_passed={"passed": 1, "total": 1, "scope": "package"})
    assert (await history(ctx, tid)).json()["items"][0]["followup"]["passed"] is True
    ctx.session.add(JudgeJob(submission_id=fixed.id, status="failed"))
    await ctx.session.flush()
    assert (await history(ctx, tid)).json()["items"][0]["followup"]["passed"] is False


async def test_general_feedback_does_not_count_unrelated_optional_retry_as_required_repair(api_context):
    ctx = api_context
    optional = Problem(pid="OPTIONAL-FOLLOWUP", title="选做", samples=[])
    ctx.session.add(optional)
    await ctx.session.flush()
    tid = (await create(ctx, publish=True, problems=[{"pid": ctx.problem.pid}, {"pid": optional.pid, "required": False}]))["id"]
    await ctx.submission(training_id=tid, verdict="AC", tests_passed={"passed": 1, "total": 1, "scope": "package"})
    assert (await feedback(ctx, tid)).status_code == 200
    await ctx.submission(training_id=tid, target_problem=optional)
    follow = (await history(ctx, tid)).json()["items"][0]["followup"]
    assert follow["has_new_submission"] is True and follow["passed"] is False


async def test_profile_and_report_only_include_accessible_student_training(api_context):
    ctx = api_context
    tid = (await create(ctx, publish=True))["id"]
    await create(ctx)  # 草稿不入档案。
    await ctx.submission(training_id=tid)
    await ctx.submission()  # 自由练习不入训练档案。
    await feedback(ctx, tid, content="仅属于学生本人的指导文字。")
    await feedback(ctx, tid, student=ctx.users.peer, content="其他学生的私有反馈不能出现。")
    for actor in (ctx.users.student, ctx.users.teacher, ctx.users.admin):
        r = await profile(ctx, actor=actor)
        assert r.status_code == 200
        assert r.json()["summary"]["trainings"] == 1 and "其他学生的私有反馈" not in r.text
        report = await ctx.client.get(f"/api/v1/students/{ctx.users.student.id}/profile/report", headers=ctx.headers(actor))
        assert report.status_code == 200 and "仅属于学生本人的指导" in report.text
        assert "其他学生的私有反馈" not in report.text and "no-store" in report.headers["cache-control"]
    assert (await profile(ctx, actor=ctx.users.peer)).status_code == 404
    assert (await profile(ctx, actor=ctx.users.other_teacher)).status_code == 404
    ctx.users.student.class_name = "B"
    await ctx.session.flush()
    assert (await profile(ctx)).status_code == 404
    assert (await profile(ctx, actor=ctx.users.student)).json()["items"] == []
    assert (await history(ctx, tid, actor=ctx.users.student)).status_code == 404


@pytest.mark.parametrize("content", [" ", "\x00", "\ud800"])
async def test_feedback_rejects_unstorable_or_blank_text(api_context, content):
    ctx = api_context
    tid = (await create(ctx, publish=True))["id"]
    assert (await feedback(ctx, tid, content=content)).status_code == 422


async def test_concurrent_feedback_retry_persists_only_one_message(api_context, isolated_database):
    sessions = async_sessionmaker(isolated_database, expire_on_commit=False)
    async with sessions() as session:
        teacher = User(username="follow_teacher", role="teacher", class_name="A", password_hash="unused")
        student = User(username="follow_student", role="student", class_name="A", password_hash="unused")
        session.add_all([teacher, student])
        await session.flush()
        task = Training(teacher_id=teacher.id, class_name="A", title="反馈重试", status="published", due_at=datetime.now(UTC) + timedelta(days=1))
        session.add(task)
        await session.flush()
        session.add(TrainingMember(training_id=task.id, student_id=student.id))
        await session.commit()
        tid, sid, uid = task.id, student.id, teacher.id
    payload = FeedbackCreate(content="并发重复发送应当只保存一次。", request_id=uuid4())
    async def write():
        async with sessions() as session:
            teacher = await session.get(User, uid)
            result = await create_feedback(session, teacher, tid, sid, payload)
            return result["items"][0]["id"]
    ids = await asyncio.gather(write(), write())
    assert ids[0] == ids[1]
    async with sessions() as session:
        assert len((await session.scalars(select(TrainingFeedback))).all()) == 1
