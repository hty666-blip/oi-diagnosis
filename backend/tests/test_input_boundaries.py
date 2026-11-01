"""真实 PostgreSQL HTTP 边界：非法编号先拒绝，合法编号与中文题号保持可用。"""

from copy import deepcopy
from urllib.parse import quote

import pytest
from sqlalchemy import func, select

from app.domain.models import (
    Diagnosis,
    DiagnosisReview,
    Evidence,
    HintSession,
    HintTurn,
    JudgeJob,
    Problem,
    Submission,
)

MAX_OBJECT_ID = 2**63 - 1
UNSAFE_OBJECT_ID = MAX_OBJECT_ID + 1
PRIVATE_CODE = "int main(){return 0;} // do-not-echo-boundary-code"


async def _row_counts(ctx):
    models = (Submission, JudgeJob, HintSession, HintTurn, Diagnosis, DiagnosisReview, Evidence)
    return tuple([
        await ctx.session.scalar(select(func.count()).select_from(model)) for model in models
    ])


def _assert_validation(response, location):
    assert response.status_code == 422, response.text
    errors = response.json()["detail"]
    assert any(error["loc"] == list(location) for error in errors), errors
    assert all("input" not in error and "ctx" not in error for error in errors), errors
    assert "do-not-echo-boundary" not in response.text
    response.text.encode("utf-8")


async def _create_session(ctx, submission):
    response = await ctx.client.post(
        "/api/v1/sessions", headers=ctx.headers(), json={"submission_id": submission.id}
    )
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


# 每个路由都验证绑定了编号约束；下一个测试独立覆盖两类编号的全部上下界。
@pytest.mark.parametrize("method,resource,suffix,payload,bad_id", [
    ("GET", "submissions", "", None, UNSAFE_OBJECT_ID),
    ("GET", "submissions", "/evidence", None, 0),
    ("POST", "submissions", "/diagnose", None, -1),
    ("GET", "submissions", "/lineage", None, UNSAFE_OBJECT_ID),
    ("GET", "submissions", "/reviews", None, 0),
    ("GET", "submissions", "/card", None, -1),
    ("POST", "submissions", "/retry", None, UNSAFE_OBJECT_ID),
    ("POST", "submissions", "/cancel", None, 0),
    ("GET", "sessions", "", None, -1),
    ("POST", "sessions", "/messages", {"content": "do-not-echo-boundary-message"}, 0),
    ("POST", "sessions", "/verify", {"submission_id": 1}, UNSAFE_OBJECT_ID),
])
async def test_each_object_path_rejects_invalid_id_and_accepts_bigint_max(
    api_context, method, resource, suffix, payload, bad_id,
):
    ctx = api_context
    before = await _row_counts(ctx)
    kwargs = {"headers": ctx.headers()}
    if payload is not None:
        kwargs["json"] = payload
    response = await ctx.client.request(
        method, f"/api/v1/{resource}/{bad_id}{suffix}", **kwargs
    )
    field = "submission_id" if resource == "submissions" else "session_id"
    _assert_validation(response, ("path", field))

    # BigInt 最大合法值必须到达正常查找逻辑，不能溢出数据库，也不能误报 422。
    maximum = await ctx.client.request(
        method, f"/api/v1/{resource}/{MAX_OBJECT_ID}{suffix}", **kwargs
    )
    assert maximum.status_code == 404, maximum.text
    assert await _row_counts(ctx) == before


@pytest.mark.parametrize("resource,field", [
    ("submissions", "submission_id"), ("sessions", "session_id"),
])
@pytest.mark.parametrize("bad_id", [0, -1, UNSAFE_OBJECT_ID])
async def test_submission_and_session_path_bounds(api_context, resource, field, bad_id):
    ctx = api_context
    before = await _row_counts(ctx)
    response = await ctx.client.get(f"/api/v1/{resource}/{bad_id}", headers=ctx.headers())
    _assert_validation(response, ("path", field))
    assert await _row_counts(ctx) == before


@pytest.mark.parametrize("bad_id", [UNSAFE_OBJECT_ID, True, 1.5, "1"],
                         ids=["overflow", "bool", "fraction", "numeric-string"])
async def test_json_object_ids_are_strict_without_writes(api_context, bad_id):
    ctx = api_context
    submission = await ctx.submission()
    evidence = Evidence(submission_id=submission.id, type="run_trace", payload={
        "input": "1", "stdout": "2", "expected": "1", "status": "Accepted", "exit_code": 0,
    })
    ctx.session.add(evidence)
    await ctx.session.flush()
    session_id = await _create_session(ctx, submission)
    create = {"problem_pid": ctx.problem.pid, "code": PRIVATE_CODE}
    review = {
        "submission_id": submission.id, "expected_revision": 0, "decision": "revise",
        "narrative": "已对照当前执行证据，请先检查输出与预期不同的位置。",
        "evidence_ids": [evidence.id],
        "mechanism": [{"code": "M10", "explain": "输出与预期不一致。",
                       "evidence_ids": [evidence.id]}],
    }
    cases = []
    for field in ("student_id", "parent_submission_id"):
        cases.append(("/submissions", {**create, field: bad_id}, ("body", field), False))
    cases.extend([
        ("/sessions", {"submission_id": bad_id}, ("body", "submission_id"), False),
        (f"/sessions/{session_id}/verify", {"submission_id": bad_id},
         ("body", "submission_id"), False),
        ("/coach/reviews", {**review, "submission_id": bad_id},
         ("body", "submission_id"), True),
        ("/coach/reviews", {**review, "evidence_ids": [bad_id]},
         ("body", "evidence_ids", 0), True),
    ])
    mechanism_review = deepcopy(review)
    mechanism_review["mechanism"][0]["evidence_ids"] = [bad_id]
    cases.append(("/coach/reviews", mechanism_review,
                  ("body", "mechanism", 0, "evidence_ids", 0), True))
    before = await _row_counts(ctx)
    for path, payload, location, teacher_only in cases:
        user = ctx.users.teacher if teacher_only else ctx.users.student
        response = await ctx.client.post(
            f"/api/v1{path}", headers=ctx.headers(user), json=payload
        )
        _assert_validation(response, location)
        assert await _row_counts(ctx) == before, path
    hint_session = await ctx.session.get(HintSession, session_id)
    assert hint_session.verified_submission_id is None
    diagnosis = await ctx.session.scalar(
        select(Diagnosis).where(Diagnosis.submission_id == submission.id)
    )
    assert diagnosis.review_revision == 0


async def test_optional_null_ids_create_normal_queued_submission(api_context):
    ctx = api_context
    before = await _row_counts(ctx)
    response = await ctx.client.post(
        "/api/v1/submissions",
        headers={**ctx.headers(), "Prefer": "respond-async"},
        json={"problem_pid": ctx.problem.pid, "code": PRIVATE_CODE,
              "student_id": None, "parent_submission_id": None},
    )
    assert response.status_code == 202, response.text
    data = response.json()
    assert data["parent_submission_id"] is None
    assert data["job"]["status"] == "queued"
    persisted = await ctx.session.get(Submission, data["id"])
    assert persisted.student_id == ctx.users.student.id
    assert persisted.parent_submission_id is None
    assert response.headers["Location"] == f"/api/v1/submissions/{persisted.id}"
    assert await _row_counts(ctx) == (before[0] + 1, before[1] + 1, *before[2:])


async def test_existing_submission_and_session_ids_remain_accessible(api_context):
    ctx = api_context
    submission = await ctx.submission()
    session_id = await _create_session(ctx, submission)
    before = await _row_counts(ctx)
    for suffix in ("", "/evidence", "/lineage", "/reviews", "/card"):
        response = await ctx.client.get(
            f"/api/v1/submissions/{submission.id}{suffix}", headers=ctx.headers()
        )
        assert response.status_code == 200, response.text
    response = await ctx.client.get(f"/api/v1/sessions/{session_id}", headers=ctx.headers())
    assert response.status_code == 200, response.text
    assert response.json()["session_id"] == session_id
    assert await _row_counts(ctx) == before


@pytest.mark.parametrize("encoded_pid", ["TEST%00BOUNDARY", "A" * 33])
async def test_problem_detail_rejects_unstorable_or_oversized_pid(api_context, encoded_pid):
    ctx = api_context
    before = await _row_counts(ctx)
    response = await ctx.client.get(f"/api/v1/problems/{encoded_pid}", headers=ctx.headers())
    _assert_validation(response, ("path", "pid"))
    assert await _row_counts(ctx) == before


async def test_chinese_and_max_length_problem_ids_remain_accessible(api_context):
    ctx = api_context
    for pid in ("中文题号-边界", "P" * 32):
        ctx.session.add(Problem(pid=pid, title="合法题号边界", samples=[], package_level="C"))
        await ctx.session.flush()
        response = await ctx.client.get(
            f"/api/v1/problems/{quote(pid, safe='')}", headers=ctx.headers()
        )
        assert response.status_code == 200, response.text
        assert response.json()["pid"] == pid
