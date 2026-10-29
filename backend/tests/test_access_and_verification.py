"""通过真实 PostgreSQL 与 HTTP 依赖验证归属、历史恢复和辅导核验。"""

import pytest
from sqlalchemy import func, select

from app.domain.models import Diagnosis, Evidence, HintSession, HintTurn, Mastery, Problem


async def _open(context, submission):
    response = await context.client.post("/api/v1/sessions", headers=context.headers(),
                                         json={"submission_id": submission.id})
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


async def test_student_cannot_read_or_mutate_other_submission(api_context):
    ctx = api_context
    own = await ctx.submission()
    foreign = await ctx.submission(ctx.users.other)
    session_id = await _open(ctx, own)
    foreign_headers = ctx.headers(ctx.users.other)
    requests = [
        ("GET", f"/submissions/{own.id}", None),
        ("GET", f"/submissions/{own.id}/evidence", None),
        ("GET", f"/submissions/{own.id}/card", None),
        ("POST", f"/submissions/{own.id}/diagnose", None),
        ("POST", "/sessions", {"submission_id": own.id}),
        ("GET", f"/sessions/{session_id}", None),
        ("POST", f"/sessions/{session_id}/messages", {"content": "改好了"}),
        ("POST", f"/sessions/{session_id}/verify", {"submission_id": foreign.id}),
    ]
    for method, path, body in requests:
        response = await ctx.client.request(method, "/api/v1" + path,
                                             headers=foreign_headers, json=body)
        assert response.status_code == 404, (path, response.text)


async def test_teacher_scope_and_admin_are_enforced(api_context):
    ctx = api_context
    same_class = await ctx.submission()
    other_class = await ctx.submission(ctx.users.other)
    for user, target, status in [
        (ctx.users.teacher, same_class, 200), (ctx.users.teacher, other_class, 404),
        (ctx.users.blank_teacher, same_class, 404), (ctx.users.admin, other_class, 200),
    ]:
        result = await ctx.client.get(f"/api/v1/submissions/{target.id}", headers=ctx.headers(user))
        assert result.status_code == status
    overview = await ctx.client.get("/api/v1/coach/overview", headers=ctx.headers(ctx.users.teacher))
    assert overview.json()["submissions"] == 1
    for submission in (same_class, other_class):
        diagnosis = await ctx.session.scalar(
            select(Diagnosis).where(Diagnosis.submission_id == submission.id)
        )
        diagnosis.status = "needs_human"
    await ctx.session.flush()
    queue = await ctx.client.get("/api/v1/coach/queue", headers=ctx.headers(ctx.users.teacher))
    assert [item["submission_id"] for item in queue.json()] == [same_class.id]
    blank = await ctx.client.get("/api/v1/coach/queue", headers=ctx.headers(ctx.users.blank_teacher))
    assert blank.json() == []


async def test_payload_cannot_impersonate_student(api_context):
    ctx = api_context
    for user in (ctx.users.student, ctx.users.teacher, ctx.users.admin):
        result = await ctx.client.post("/api/v1/submissions", headers=ctx.headers(user), json={
            "problem_pid": ctx.problem.pid, "code": "int main(){}", "student_id": ctx.users.other.id,
        })
        assert result.status_code == 403


async def test_history_restores_code_and_legacy_ac_is_scoped(api_context):
    ctx = api_context
    original = await ctx.submission(verdict="AC", tests_passed={"passed": 1, "total": 1})
    result = await ctx.client.get(f"/api/v1/submissions/{original.id}", headers=ctx.headers())
    assert result.json()["verdict"] == "SAMPLE_PASS"
    assert result.json()["code"] == original.code
    assert result.json()["problem_pid"] == ctx.problem.pid
    history = await ctx.client.get("/api/v1/me/submissions", headers=ctx.headers())
    assert history.json()[0]["problem_title"] == ctx.problem.title
    assert history.json()[0]["verdict"] == "SAMPLE_PASS"


async def test_diagnosis_requires_finished_execution(api_context):
    ctx = api_context
    pending = await ctx.submission(stage="RUNNING", verdict=None)
    result = await ctx.client.post(f"/api/v1/submissions/{pending.id}/diagnose", headers=ctx.headers())
    assert result.status_code == 409


async def test_self_report_does_not_resolve_or_change_mastery(api_context):
    ctx = api_context
    original = await ctx.submission()
    session_id = await _open(ctx, original)
    duplicate_id = await _open(ctx, original)
    assert duplicate_id == session_id
    result = await ctx.client.post(f"/api/v1/sessions/{session_id}/messages", headers=ctx.headers(),
                                   json={"content": "改好了，已经通过了"})
    assert result.json()["status"] == "open"
    assert result.json()["action"] == "verify"
    assert (await ctx.session.scalar(select(func.count()).select_from(Mastery))) == 0


async def test_verification_requires_new_same_problem_submission(api_context):
    ctx = api_context
    original = await ctx.submission()
    session_id = await _open(ctx, original)
    result = await ctx.client.post(f"/api/v1/sessions/{session_id}/verify", headers=ctx.headers(),
                                   json={"submission_id": original.id})
    assert result.status_code == 409
    another_problem = Problem(pid="TEST-OTHER", title="另一题")
    ctx.session.add(another_problem)
    await ctx.session.flush()
    wrong_problem = await ctx.submission(target_problem=another_problem)
    result = await ctx.client.post(f"/api/v1/sessions/{session_id}/verify", headers=ctx.headers(),
                                   json={"submission_id": wrong_problem.id})
    assert result.status_code == 409
    other_student = await ctx.submission(ctx.users.other)
    result = await ctx.client.post(f"/api/v1/sessions/{session_id}/verify", headers=ctx.headers(),
                                   json={"submission_id": other_student.id})
    assert result.status_code == 404


async def test_sample_pass_stays_open_package_ac_resolves_idempotently(api_context):
    ctx = api_context
    original = await ctx.submission(code_hash="original")
    session_id = await _open(ctx, original)
    sample = await ctx.submission(code_hash="sample", verdict="SAMPLE_PASS",
                                 tests_passed={"passed": 1, "total": 1, "scope": "samples"})
    result = await ctx.client.post(f"/api/v1/sessions/{session_id}/verify", headers=ctx.headers(),
                                   json={"submission_id": sample.id})
    assert result.json()["status"] == "open"
    complete = await ctx.submission(code_hash="complete", verdict="AC",
                                   tests_passed={"passed": 3, "total": 3, "scope": "package"})
    payload = {"submission_id": complete.id}
    first = await ctx.client.post(f"/api/v1/sessions/{session_id}/verify", headers=ctx.headers(), json=payload)
    assert first.json()["status"] == "resolved"
    turns_before = await ctx.session.scalar(select(func.count()).select_from(HintTurn))
    repeated = await ctx.client.post(f"/api/v1/sessions/{session_id}/verify", headers=ctx.headers(), json=payload)
    assert repeated.json()["status"] == "resolved"
    assert repeated.json()["verified_submission_id"] == complete.id
    assert await ctx.session.scalar(select(func.count()).select_from(HintTurn)) == turns_before
    hs = await ctx.session.get(HintSession, session_id)
    assert hs.verified_submission_id == complete.id
    assert await ctx.session.scalar(select(func.count()).select_from(Mastery)) == 0


async def test_failed_submission_is_counted_once_and_future_hints_use_it(api_context):
    ctx = api_context
    original = await ctx.submission(code_hash="original")
    session_id = await _open(ctx, original)
    modified = await ctx.submission(code_hash="modified")
    diagnosis = await ctx.session.scalar(
        select(Diagnosis).where(Diagnosis.submission_id == modified.id)
    )
    diagnosis.nature = [{"p": "P1", "code": "P1-X", "line": 17,
                         "message": "修改后这一行的编译器证据", "hint": "核对分号"}]
    ctx.session.add(Evidence(submission_id=modified.id, type="run_trace", payload={
        "status": "Accepted", "stdout": "4", "expected": "3", "input": "1 2\n",
    }))
    await ctx.session.flush()
    for _ in range(2):
        response = await ctx.client.post(f"/api/v1/sessions/{session_id}/verify", headers=ctx.headers(),
                                         json={"submission_id": modified.id})
        assert response.json()["status"] == "open"
    hs = await ctx.session.get(HintSession, session_id)
    assert hs.failed_attempts == 1
    hs.level = 1
    await ctx.session.flush()
    response = await ctx.client.post(f"/api/v1/sessions/{session_id}/messages", headers=ctx.headers(),
                                     json={"content": "我检查输出以后还是没发现原因"})
    assert "17" in response.json()["reply"]


async def test_passing_output_with_trailing_space_is_not_used_as_counterexample(api_context):
    from app.services.tutor_service import TutorService

    ctx = api_context
    original = await ctx.submission()
    diagnosis = await ctx.session.scalar(
        select(Diagnosis).where(Diagnosis.submission_id == original.id)
    )
    ctx.session.add(Evidence(submission_id=original.id, type="run_trace", payload={
        "status": "Accepted", "stdout": "3 \n", "expected": "3", "input": "1 2\n",
    }))
    await ctx.session.flush()
    view = await TutorService(ctx.session, ctx.users.student)._diagnosis_view(diagnosis, original.id)
    assert view.counterexample == ""


@pytest.mark.parametrize("verdict,counts", [
    ("AC", {"passed": 0, "total": 0}),
    ("AC", {"passed": 1, "total": 1}),
    ("NO_TESTS", {"passed": 0, "total": 0, "scope": "samples"}),
])
async def test_legacy_or_empty_tests_cannot_resolve(api_context, verdict, counts):
    ctx = api_context
    original = await ctx.submission(code_hash="original")
    session_id = await _open(ctx, original)
    modified = await ctx.submission(code_hash="modified", verdict=verdict, tests_passed=counts)
    response = await ctx.client.post(f"/api/v1/sessions/{session_id}/verify", headers=ctx.headers(),
                                     json={"submission_id": modified.id})
    assert response.status_code == 200
    assert response.json()["status"] == "open"


async def test_problem_search_applies_before_limit(api_context):
    ctx = api_context
    ctx.session.add(Problem(pid="TEST-LAST-SEARCH", title="唯一搜索结果"))
    await ctx.session.flush()
    response = await ctx.client.get("/api/v1/problems?q=TEST-LAST-SEARCH&limit=1", headers=ctx.headers())
    assert [row["pid"] for row in response.json()] == ["TEST-LAST-SEARCH"]
