import json

import pytest
from sqlalchemy import func, select

from app.core.text_validation import storage_text
from app.domain.models import Diagnosis, DiagnosisReview, HintTurn


@pytest.mark.parametrize("value", ["\ufeff# 注释\n  int x = 1;  \n", "中文与 emoji 😀", "", "a\tb\r\nc"])
def test_storage_validation_does_not_normalize_valid_text(value):
    assert storage_text(value) == value


@pytest.mark.parametrize("value", ["abc\x00def", "bad\ud800text", "bad\udffftext"])
def test_invalid_text_is_rejected_without_echo(value):
    with pytest.raises(ValueError) as error:
        storage_text(value)
    assert value not in str(error.value)


@pytest.mark.parametrize("bad", ["\x00", "\ud800"])
async def test_bad_tutor_message_returns_422_without_adding_turns(api_context, bad):
    ctx = api_context
    sub = await ctx.submission()
    opened = await ctx.client.post("/api/v1/sessions", headers=ctx.headers(), json={"submission_id": sub.id})
    count_before = await ctx.session.scalar(select(func.count()).select_from(HintTurn))
    response = await ctx.client.post(f"/api/v1/sessions/{opened.json()['session_id']}/messages",
                                      headers={**ctx.headers(), "Content-Type": "application/json"},
                                      content=json.dumps({"content": bad}))
    assert response.status_code == 422
    assert await ctx.session.scalar(select(func.count()).select_from(HintTurn)) == count_before


@pytest.mark.parametrize("bad", ["说明包含\x00空字符", "说明包含\ud800坏字符"])
async def test_bad_review_returns_422_without_writing_audit(api_context, bad):
    ctx = api_context
    sub = await ctx.submission()
    response = await ctx.client.post("/api/v1/coach/reviews",
                                    headers={**ctx.headers(ctx.users.teacher), "Content-Type": "application/json"},
                                    content=json.dumps({
        "submission_id": sub.id, "decision": "return", "expected_revision": 0,
        "narrative": bad, "evidence_ids": [],
    }))
    assert response.status_code == 422
    assert await ctx.session.scalar(select(func.count()).select_from(DiagnosisReview)) == 0
    diag = await ctx.session.scalar(select(Diagnosis).where(Diagnosis.submission_id == sub.id))
    assert diag.review_revision == 0


async def test_null_search_text_never_reaches_postgres(api_context):
    response = await api_context.client.get("/api/v1/problems?q=%00", headers=api_context.headers())
    assert response.status_code == 422
