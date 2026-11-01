"""存量测试计数必须能证明正式通过，布尔值不能冒充执行次数。"""

import pytest
from sqlalchemy import select

from app.domain.models import Diagnosis, HintSession


@pytest.mark.parametrize("counts", [
    {"scope": "package", "total": True, "passed": 1},
    {"scope": "package", "total": 1, "passed": True},
    {"scope": "package", "total": 1.0, "passed": 1},
    {"scope": "package", "total": 1, "passed": 1.0},
    {"scope": "package", "total": 1, "passed": -1},
    {"scope": "package", "total": 2, "passed": 1},
    ["malformed-legacy-summary"],
    "malformed-legacy-summary",
], ids=["boolean-total", "boolean-passed", "float-total", "float-passed",
        "negative", "partial", "list", "string"])
async def test_invalid_legacy_counts_cannot_display_ac_or_resolve_tutoring(api_context, counts):
    ctx = api_context
    original = await ctx.submission(code_hash="original")
    opened = await ctx.client.post(
        "/api/v1/sessions", headers=ctx.headers(), json={"submission_id": original.id},
    )
    assert opened.status_code == 200, opened.text
    session_id = opened.json()["session_id"]
    candidate = await ctx.submission(
        code_hash="modified", verdict="AC", tests_passed=counts,
    )

    detail = await ctx.client.get(
        f"/api/v1/submissions/{candidate.id}", headers=ctx.headers(),
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["verdict"] == "UNVERIFIED"
    assert "请重新提交验证" in detail.json()["diagnosis"]["narrative"]
    history = await ctx.client.get("/api/v1/me/submissions", headers=ctx.headers())
    assert history.status_code == 200, history.text
    record = next(row for row in history.json() if row["id"] == candidate.id)
    assert record["verdict"] == detail.json()["verdict"]
    lineage = await ctx.client.get(f"/api/v1/submissions/{candidate.id}/lineage", headers=ctx.headers())
    assert lineage.status_code == 200, lineage.text
    assert lineage.json()[-1]["verdict"] == detail.json()["verdict"]
    assert lineage.json()[-1]["tests_passed"] == detail.json()["tests_passed"]
    card = await ctx.client.get(f"/api/v1/submissions/{candidate.id}/card", headers=ctx.headers())
    assert card.status_code == 200 and "历史结果待核对" in card.text

    verified = await ctx.client.post(
        f"/api/v1/sessions/{session_id}/verify", headers=ctx.headers(),
        json={"submission_id": candidate.id},
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "open"
    assert verified.json()["action"] == "verify"
    assert verified.json()["verdict"] == detail.json()["verdict"]
    restored = await ctx.client.get(f"/api/v1/sessions/{session_id}", headers=ctx.headers())
    assert restored.status_code == 200, restored.text
    assert restored.json()["status"] == "open"
    assert (await ctx.session.get(HintSession, session_id)).failed_attempts == 0
    assert candidate.verdict == "AC"


async def test_integer_package_counts_still_display_ac_and_resolve_tutoring(api_context):
    ctx = api_context
    original = await ctx.submission(code_hash="original")
    opened = await ctx.client.post(
        "/api/v1/sessions", headers=ctx.headers(), json={"submission_id": original.id},
    )
    assert opened.status_code == 200, opened.text
    session_id = opened.json()["session_id"]
    candidate = await ctx.submission(
        code_hash="modified", verdict="AC",
        tests_passed={"scope": "package", "total": 1, "passed": 1},
    )

    detail = await ctx.client.get(
        f"/api/v1/submissions/{candidate.id}", headers=ctx.headers(),
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["verdict"] == "AC"
    verified = await ctx.client.post(
        f"/api/v1/sessions/{session_id}/verify", headers=ctx.headers(),
        json={"submission_id": candidate.id},
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "resolved"
    assert verified.json()["verified_submission_id"] == candidate.id


@pytest.mark.parametrize("total, expected_verdict", [(True, "UNVERIFIED"), (1, "AC")],
                         ids=["untrusted-counts", "verified-counts"])
async def test_teacher_queue_uses_current_result_scope_without_rewriting_history(
    api_context, total, expected_verdict,
):
    ctx = api_context
    submission = await ctx.submission(
        verdict="AC", tests_passed={"scope": "package", "total": total, "passed": 1},
    )
    diagnosis = await ctx.session.scalar(
        select(Diagnosis).where(Diagnosis.submission_id == submission.id)
    )
    original_narrative = "当前题包测试全部通过，本轮无需继续修正。"
    diagnosis.status = "needs_human"
    diagnosis.narrative = original_narrative
    await ctx.session.flush()

    queue = await ctx.client.get("/api/v1/coach/queue", headers=ctx.headers(ctx.users.teacher))
    assert queue.status_code == 200, queue.text
    assert len(queue.json()) == 1
    record = queue.json()[0]
    assert record["submission_id"] == submission.id
    assert record["verdict"] == expected_verdict
    detail = await ctx.client.get(f"/api/v1/submissions/{submission.id}", headers=ctx.headers())
    assert detail.status_code == 200, detail.text
    assert record["narrative"] == detail.json()["diagnosis"]["narrative"]
    if expected_verdict == "UNVERIFIED":
        assert "请重新提交验证" in record["narrative"]
    else:
        assert record["narrative"] == original_narrative
    await ctx.session.refresh(diagnosis)
    assert diagnosis.narrative == original_narrative
    assert submission.verdict == "AC"


@pytest.mark.parametrize("invalid", ["counts", "stage", "missing_binding"])
async def test_resolved_history_with_untrusted_counts_never_claims_still_verified(api_context, invalid):
    ctx = api_context
    original = await ctx.submission(code_hash="original")
    opened = await ctx.client.post("/api/v1/sessions", headers=ctx.headers(),
                                   json={"submission_id": original.id})
    session_id = opened.json()["session_id"]
    candidate = await ctx.submission(code_hash="changed", verdict="AC",
                                    tests_passed={"scope": "package", "total": 1, "passed": 1})
    endpoint = f"/api/v1/sessions/{session_id}"
    completed = await ctx.client.post(endpoint + "/verify", headers=ctx.headers(),
                                      json={"submission_id": candidate.id})
    assert completed.json()["status"] == "resolved"
    if invalid == "counts":
        candidate.tests_passed = {"scope": "package", "total": True, "passed": 1}
    elif invalid == "stage":
        candidate.stage, candidate.verdict = "RUNNING", None
    else:
        (await ctx.session.get(HintSession, session_id)).verified_submission_id = None
    await ctx.session.flush()
    restored = await ctx.client.get(endpoint, headers=ctx.headers())
    assert restored.json()["status"] == "unverified"
    repeated = await ctx.client.post(endpoint + "/verify", headers=ctx.headers(),
                                     json={"submission_id": candidate.id})
    if invalid == "counts":
        assert repeated.json()["status"] == "unverified"
        assert repeated.json()["action"] != "resolved"
    else:
        assert repeated.status_code == 409
    reply = await ctx.client.post(endpoint + "/messages", headers=ctx.headers(),
                                  json={"content": "已经通过了吗"})
    assert reply.json()["status"] == "unverified"
    assert "待核对" in reply.json()["reply"]
    assert (await ctx.session.get(HintSession, session_id)).status == "resolved"
    assert candidate.verdict == (None if invalid == "stage" else "AC")
