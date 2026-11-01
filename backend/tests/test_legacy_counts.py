"""存量测试计数必须能证明正式通过，布尔值不能冒充执行次数。"""

import pytest

from app.domain.models import HintSession


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
