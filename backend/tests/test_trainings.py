"""独立 MySQL：训练发布、名单归属、任务隔离与真实完成口径。"""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.domain.models import JudgeJob, Problem, TrainingMember, User
from app.services.submission_service import SubmissionService


def payload(ctx, **changes):
    return {"title": "循环训练", "class_name": "A", "objective": "理解累计更新",
            "description": "先完成必做题", "due_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
            "problems": [{"pid": ctx.problem.pid, "required": True}], **changes}


async def create(ctx, publish=False, **changes):
    r = await ctx.client.post("/api/v1/trainings", headers=ctx.headers(ctx.users.teacher), json=payload(ctx, **changes))
    assert r.status_code == 200, r.text
    if publish:
        r = await ctx.client.post(f"/api/v1/trainings/{r.json()['id']}/publish", headers=ctx.headers(ctx.users.teacher))
        assert r.status_code == 200, r.text
    return r.json()


async def test_draft_preview_publish_and_student_receives_only_own_class(api_context):
    ctx = api_context
    draft = await create(ctx)
    tid = draft["id"]
    assert draft["problems"][0]["pid"] == ctx.problem.pid
    assert (await ctx.client.get("/api/v1/trainings", headers=ctx.headers())).json() == []
    assert (await ctx.client.get(f"/api/v1/trainings/{tid}", headers=ctx.headers())).status_code == 404
    r = await ctx.client.post(f"/api/v1/trainings/{tid}/draft", headers=ctx.headers(ctx.users.teacher), json=payload(ctx, title="修改后的草稿"))
    assert r.status_code == 200 and r.json()["title"] == "修改后的草稿"
    published = await ctx.client.post(f"/api/v1/trainings/{tid}/publish", headers=ctx.headers(ctx.users.teacher))
    assert published.json()["summary"] == {"students": 2, "participated": 0, "completed": 0, "late_submissions": 0}
    own = (await ctx.client.get(f"/api/v1/trainings/{tid}", headers=ctx.headers())).json()
    assert len(own["rows"]) == 1 and own["rows"][0]["student_id"] == ctx.users.student.id
    assert (await ctx.client.get(f"/api/v1/trainings/{tid}", headers=ctx.headers(ctx.users.other))).status_code == 404
    assert len((await ctx.client.get("/api/v1/trainings", headers=ctx.headers())).json()) == 1
    assert (await ctx.client.post(f"/api/v1/trainings/{tid}/draft", headers=ctx.headers(ctx.users.teacher), json=payload(ctx))).status_code == 409


async def test_roster_and_writes_have_server_side_role_and_class_scope(api_context):
    ctx = api_context
    r = await ctx.client.get("/api/v1/trainings/roster", headers=ctx.headers(ctx.users.teacher))
    assert {row["id"] for row in r.json()} == {ctx.users.student.id, ctx.users.peer.id}
    assert (await ctx.client.get("/api/v1/trainings/roster", headers=ctx.headers())).status_code == 403
    assert (await ctx.client.get("/api/v1/trainings/roster", headers=ctx.headers(ctx.users.blank_teacher))).json() == []
    assert (await ctx.client.post("/api/v1/trainings", headers=ctx.headers(), json=payload(ctx))).status_code == 403
    assert (await ctx.client.post("/api/v1/trainings", headers=ctx.headers(ctx.users.other_teacher), json=payload(ctx))).status_code == 403
    training = await create(ctx, publish=True)
    for actor in (ctx.users.other_teacher, ctx.users.blank_teacher, ctx.users.other):
        assert (await ctx.client.get(f"/api/v1/trainings/{training['id']}", headers=ctx.headers(actor))).status_code == 404


@pytest.mark.parametrize("changes", [
    {"title": "  "}, {"class_name": ""}, {"due_at": "2020-01-01T00:00:00Z"},
    {"due_at": "2030-01-01T00:00:00"}, {"problems": []},
    {"problems": [{"pid": "missing"}]},
])
async def test_invalid_training_does_not_create_partial_task(api_context, changes):
    ctx = api_context
    r = await ctx.client.post("/api/v1/trainings", headers=ctx.headers(ctx.users.teacher), json=payload(ctx, **changes))
    assert r.status_code == 422, r.text
    assert (await ctx.client.get("/api/v1/trainings", headers=ctx.headers(ctx.users.teacher))).json() == []


async def test_duplicate_problems_and_no_required_problem_rejected(api_context):
    ctx = api_context
    for problems in ([{"pid": ctx.problem.pid}] * 2, [{"pid": ctx.problem.pid, "required": False}]):
        r = await ctx.client.post("/api/v1/trainings", headers=ctx.headers(ctx.users.teacher), json=payload(ctx, problems=problems))
        assert r.status_code == 422


async def test_publish_snapshot_is_idempotent_new_students_not_silently_added(api_context):
    ctx = api_context
    task = await create(ctx, publish=True)
    newcomer = User(username="newcomer", role="student", class_name="A", password_hash="unused")
    ctx.session.add(newcomer)
    await ctx.session.flush()
    r = await ctx.client.post(f"/api/v1/trainings/{task['id']}/publish", headers=ctx.headers(ctx.users.teacher))
    assert r.status_code == 200 and r.json()["summary"]["students"] == 2
    assert len((await ctx.session.scalars(select(TrainingMember).where(TrainingMember.training_id == task["id"]))).all()) == 2
    assert (await ctx.client.get(f"/api/v1/trainings/{task['id']}", headers=ctx.headers(newcomer))).status_code == 404
    ctx.users.student.class_name = "B"
    await ctx.session.flush()
    assert (await ctx.client.get(f"/api/v1/trainings/{task['id']}", headers=ctx.headers())).status_code == 404


async def test_matrix_uses_latest_bound_submission_and_distinguishes_scope_failure_and_late(api_context):
    ctx = api_context
    extra = Problem(pid="TEST-OPTIONAL", title="选做", samples=[])
    ctx.session.add(extra)
    await ctx.session.flush()
    task = await create(ctx, publish=True, problems=[{"pid": ctx.problem.pid}, {"pid": extra.pid, "required": False}])
    tid = task["id"]
    passed = {"passed": 1, "total": 1, "scope": "package"}
    await ctx.submission(verdict="AC", tests_passed=passed)  # 自由练习不能计入训练。
    await ctx.submission(training_id=tid, verdict="AC", tests_passed=passed)
    latest = await ctx.submission(training_id=tid, verdict="AC", tests_passed={**passed, "scope": "samples"})
    peer = await ctx.submission(user=ctx.users.peer, training_id=tid, verdict="AC", tests_passed=passed,
                                created_at=datetime.now(UTC) + timedelta(days=3))
    r = (await ctx.client.get(f"/api/v1/trainings/{tid}", headers=ctx.headers(ctx.users.teacher))).json()
    assert r["summary"] == {"students": 2, "participated": 2, "completed": 1, "late_submissions": 1}
    assert r["rows"][0]["cells"][0]["id"] == latest.id and r["rows"][0]["cells"][0]["verdict"] == "SAMPLE_PASS"
    assert r["rows"][1]["complete"] is True and r["rows"][1]["cells"][0]["late"] is True
    assert r["rows"][0]["cells"][1] is None
    ctx.session.add(JudgeJob(submission_id=peer.id, status="failed"))
    await ctx.session.flush()
    r = (await ctx.client.get(f"/api/v1/trainings/{tid}", headers=ctx.headers(ctx.users.teacher))).json()
    assert r["summary"]["completed"] == 0 and r["rows"][1]["cells"][0]["state"] == "failed"


async def test_training_submit_is_explicit_authorized_and_cache_isolated(api_context):
    ctx = api_context
    task = await create(ctx, publish=True)
    other_task = await create(ctx, publish=True)
    service = SubmissionService(ctx.session)
    kwargs = {"language": "cpp17", "background": True}
    free = await service.create(ctx.problem.pid, "int main(){}", ctx.users.student.id, **kwargs)
    bound = await service.create(ctx.problem.pid, "int main(){}", ctx.users.student.id, training_id=task["id"], **kwargs)
    again = await service.create(ctx.problem.pid, "int main(){}", ctx.users.student.id, training_id=task["id"], **kwargs)
    third = await service.create(ctx.problem.pid, "int main(){}", ctx.users.student.id, training_id=other_task["id"], **kwargs)
    assert len({free.id, bound.id, third.id}) == 3 and again.id == bound.id
    with pytest.raises(ValueError, match="同一次训练"):
        await service.create(ctx.problem.pid, "int main(){return 1;}", ctx.users.student.id, free.id, training_id=task["id"], **kwargs)
    body = {"problem_pid": ctx.problem.pid, "code": "int main(){}", "training_id": task["id"]}
    for actor in (ctx.users.other, ctx.users.teacher):
        r = await ctx.client.post("/api/v1/submissions", headers={**ctx.headers(actor), "Prefer": "respond-async"}, json=body)
        assert r.status_code in (403, 404)
    r = await ctx.client.post("/api/v1/submissions", headers={**ctx.headers(), "Prefer": "respond-async"}, json=body)
    assert r.status_code == 202 and r.json()["training_id"] == task["id"] and r.json()["id"] == bound.id
    missing = Problem(pid="TEST-OUTSIDE", title="其他题目", samples=[])
    ctx.session.add(missing)
    await ctx.session.flush()
    r = await ctx.client.post("/api/v1/submissions", headers=ctx.headers(), json={**body, "problem_pid": missing.pid})
    assert r.status_code == 422


async def test_draft_cannot_receive_submissions_or_be_published_by_student(api_context):
    ctx = api_context
    task = await create(ctx)
    body = {"problem_pid": ctx.problem.pid, "code": "int main(){}", "training_id": task["id"]}
    assert (await ctx.client.post("/api/v1/submissions", headers=ctx.headers(), json=body)).status_code == 404
    assert (await ctx.client.post(f"/api/v1/trainings/{task['id']}/publish", headers=ctx.headers())).status_code == 403


async def test_other_training_cannot_close_this_training_tutor_session(api_context):
    ctx = api_context
    task = await create(ctx, publish=True)
    original = await ctx.submission(training_id=task["id"], code_hash="wrong")
    opened = await ctx.client.post("/api/v1/sessions", headers=ctx.headers(), json={"submission_id": original.id})
    passing = await ctx.submission(code_hash="fixed", verdict="AC", tests_passed={"passed": 1, "total": 1, "scope": "package"})
    r = await ctx.client.post(f"/api/v1/sessions/{opened.json()['session_id']}/verify", headers=ctx.headers(), json={"submission_id": passing.id})
    assert r.status_code == 409 and "同一次训练" in r.text
