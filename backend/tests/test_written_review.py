"""自编题验证人工审核、版本隔离、重放与学生端答案边界。"""

from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from test_written_bank_import import PNG, make_records, make_zip, preview

from app.domain.models import (
    WrittenCandidate,
    WrittenImportAsset,
    WrittenImportChunk,
    WrittenQuestion,
    WrittenQuestionVersion,
    WrittenReviewRevision,
)
from app.services.written_bank_storage import load_snapshot
from app.services.written_review import ReviewInput, validate_approval


async def staged(ctx, records=None):
    batch = await preview(ctx, make_zip(records))
    root = f"/api/v1/admin/written-bank/batches/{batch['id']}"
    response = await ctx.client.post(root + "/stage", headers=ctx.headers(ctx.users.admin))
    assert response.status_code == 200, response.text
    return root


async def detail(ctx, root, qid="q1"):
    response = await ctx.client.get(
        root + f"/questions/{qid}", headers=ctx.headers(ctx.users.admin)
    )
    assert response.status_code == 200, response.text
    return response.json()


def approval(draft):
    draft = deepcopy(draft)
    draft.update(
        score=2,
        scoring_rule="all_or_nothing",
        explanation="一加一等于二，因此选择选项 A。",
        answer_reference="自编算术题，已人工逐项核对",
    )
    return {
        "expected_revision": 0,
        "request_id": str(uuid4()),
        "action": "approve",
        "note": "人工核对通过",
        "rights_confirmed": True,
        "draft": draft,
        "resolutions": {},
    }


async def review(ctx, root, body, qid="q1", user=None):
    return await ctx.client.post(
        root + f"/questions/{qid}/review", json=body, headers=ctx.headers(user or ctx.users.admin)
    )


async def count(ctx, model):
    return await ctx.session.scalar(select(func.count()).select_from(model))


async def test_approval_is_replayable_and_student_responses_exclude_secrets(api_context):
    ctx = api_context
    root = await staged(ctx)
    before = await detail(ctx, root)
    assert before["review"]["status"] == "needs_review"
    catalog = await ctx.client.get("/api/v1/written/questions", headers=ctx.headers())
    assert catalog.json()["items"] == []
    body = approval(before["review"]["draft"])
    result = await review(ctx, root, body)
    assert result.status_code == 200, result.text
    pub = result.json()["review"]["publication"]
    assert pub["is_active"] and pub["revision"] == 1
    again = await review(ctx, root, body)
    assert again.status_code == 200 and again.json()["replayed"]
    assert await count(ctx, WrittenReviewRevision) == await count(ctx, WrittenQuestionVersion) == 1
    changed = deepcopy(body)
    changed["note"] = "同一编号更改内容"
    assert (await review(ctx, root, changed)).status_code == 409
    changed["request_id"] = str(uuid4())
    assert (await review(ctx, root, changed)).status_code == 409
    candidate = await ctx.session.scalar(
        select(WrittenCandidate).where(WrittenCandidate.external_id == "q1")
    )
    assert candidate.record == before["record"]
    question = await ctx.session.get(WrittenQuestion, pub["id"])
    question.public_metadata = {
        **question.public_metadata,
        "answer": "must-not-leak",
        "review_note": "private",
    }
    await ctx.session.commit()
    catalog = (await ctx.client.get("/api/v1/written/questions", headers=ctx.headers())).json()
    assert len(catalog["items"]) == 1
    view = (
        await ctx.client.get(f"/api/v1/written/questions/{pub['id']}", headers=ctx.headers())
    ).json()
    assert view["stem"] == "1+1=?" and len(view["options"]) == 2
    for response in [catalog, view]:
        serialized = str(response)
        for secret in [
            "answer",
            "explanation",
            "review_note",
            "resolutions",
            "must-not-leak",
            "source_locator",
        ]:
            assert secret not in serialized
    image = await ctx.client.get(view["assets"][0], headers=ctx.headers())
    assert image.status_code == 200 and image.content == PNG
    assert image.headers["Cache-Control"] == "no-store"
    assert (await ctx.client.get(view["assets"][0])).status_code == 401
    save = {**deepcopy(body), "action": "save", "request_id": str(uuid4()), "expected_revision": 1}
    assert (await review(ctx, root, save)).status_code == 200
    late_replay = (await review(ctx, root, body)).json()
    assert late_replay["replayed"] and late_replay["applied_revision"] == 1
    assert late_replay["review"]["revision"] == 2
    assert late_replay["review"]["status"] == "needs_review"
    assert not late_replay["review"]["publication"]["is_active"]


async def test_edits_unpublish_and_history_versions_survive_withdrawal(api_context):
    ctx = api_context
    root = await staged(ctx)
    body = approval((await detail(ctx, root))["review"]["draft"])
    first = (await review(ctx, root, body)).json()["review"]
    qid = first["publication"]["id"]
    first_view = (
        await ctx.client.get(f"/api/v1/written/questions/{qid}", headers=ctx.headers())
    ).json()
    old_version = await ctx.session.scalar(select(WrittenQuestionVersion))
    old_snapshot = await load_snapshot(
        ctx.session, old_version.batch_id, old_version.snapshot_metadata
    )
    body.update(
        request_id=str(uuid4()), expected_revision=1, action="save", note="修订题干和正确答案"
    )
    body["draft"].update(stem="3+0=?", answer={"kind": "option", "option_ids": ["B"]})
    saved = await review(ctx, root, body)
    assert saved.status_code == 200 and saved.json()["review"]["status"] == "needs_review"
    assert (
        await ctx.client.get(f"/api/v1/written/questions/{qid}", headers=ctx.headers())
    ).status_code == 404
    state_url = f"/api/v1/admin/written-bank/published/{qid}/state"
    pub = saved.json()["review"]["publication"]
    forbidden = await ctx.client.post(
        state_url,
        json={"expected_version": pub["catalog_version"], "is_active": True},
        headers=ctx.headers(ctx.users.admin),
    )
    assert forbidden.status_code == 409
    search = await ctx.client.get(
        root + "/questions?q=3%2B0&state=needs_review", headers=ctx.headers(ctx.users.admin)
    )
    assert search.json()["total"] == 1
    assert search.json()["items"][0]["stem"] == "3+0=?"
    body.update(
        request_id=str(uuid4()), expected_revision=2, action="approve", note="复核修订后的正确答案"
    )
    updated = await review(ctx, root, body)
    assert updated.status_code == 200, updated.text
    pub = updated.json()["review"]["publication"]
    assert pub["revision"] == 2
    assert (
        await load_snapshot(ctx.session, old_version.batch_id, old_version.snapshot_metadata)
        == old_snapshot
    )
    assert old_snapshot["answer"]["option_ids"] == ["A"]
    assert (await ctx.client.get(first_view["assets"][0], headers=ctx.headers())).status_code == 409
    history = (
        await ctx.client.get(root + "/questions/q1/history", headers=ctx.headers(ctx.users.admin))
    ).json()
    assert [r["revision"] for r in history["items"]] == [3, 2, 1]
    old_review = (
        await ctx.client.get(root + "/questions/q1/history/1", headers=ctx.headers(ctx.users.admin))
    ).json()
    assert old_review["snapshot"]["draft"]["stem"] == "1+1=?"
    withdrawal = {
        "request_id": str(uuid4()),
        "expected_revision": 3,
        "action": "withdraw",
        "note": "题目存在待澄清歧义",
    }
    withdrawn = await review(ctx, root, withdrawal)
    assert withdrawn.status_code == 200 and withdrawn.json()["review"]["status"] == "withdrawn"
    body.update(request_id=str(uuid4()), expected_revision=4)
    assert (await review(ctx, root, body)).status_code == 409
    withdrawal.update(
        request_id=str(uuid4()), expected_revision=4, action="reopen", note="歧义已确认，恢复审核"
    )
    assert (await review(ctx, root, withdrawal)).json()["review"]["status"] == "needs_review"
    assert (
        await ctx.client.get(f"/api/v1/written/questions/{qid}", headers=ctx.headers())
    ).status_code == 404


async def test_only_admin_can_review_history_or_change_publication(api_context):
    ctx = api_context
    root = await staged(ctx)
    body = approval((await detail(ctx, root))["review"]["draft"])
    for user in [ctx.users.student, ctx.users.teacher]:
        assert (await review(ctx, root, body, user=user)).status_code == 403
        assert (
            await ctx.client.get(root + "/questions/q1/history", headers=ctx.headers(user))
        ).status_code == 403
        assert (
            await ctx.client.get(root + "/questions/q1/history/1", headers=ctx.headers(user))
        ).status_code == 403
        assert (
            await ctx.client.post(
                "/api/v1/admin/written-bank/published/1/state",
                json={"expected_version": 0, "is_active": True},
                headers=ctx.headers(user),
            )
        ).status_code == 403


@pytest.mark.parametrize("damage", ["missing_asset", "broken_chunk"])
async def test_approval_rejects_missing_or_corrupt_images(api_context, damage):
    ctx = api_context
    root = await staged(ctx)
    body = approval((await detail(ctx, root))["review"]["draft"])
    if damage == "missing_asset":
        await ctx.session.delete(await ctx.session.scalar(select(WrittenImportAsset)))
    else:
        chunk = await ctx.session.scalar(
            select(WrittenImportChunk).where(WrittenImportChunk.key == "assets/test.png")
        )
        chunk.content = "broken"
    await ctx.session.commit()
    response = await review(ctx, root, body)
    assert response.status_code == 409
    assert await count(ctx, WrittenReviewRevision) == await count(ctx, WrittenQuestion) == 0


async def test_partial_failure_rolls_back_review_version_and_snapshot(api_context, monkeypatch):
    from app.api.v1 import admin_written

    ctx = api_context
    root = await staged(ctx)
    body = approval((await detail(ctx, root))["review"]["draft"])
    initial_chunks = await count(ctx, WrittenImportChunk)
    original = admin_written.apply_review

    async def fail_after_flush(*args):
        await original(*args)
        raise HTTPException(503, "合成故障")

    monkeypatch.setattr(admin_written, "apply_review", fail_after_flush)
    response = await review(ctx, root, body)
    assert response.status_code == 503
    await ctx.session.rollback()
    await ctx.session.refresh(ctx.users.admin)
    assert await count(ctx, WrittenReviewRevision) == await count(ctx, WrittenQuestionVersion) == 0
    assert await count(ctx, WrittenImportChunk) == initial_chunks
    assert (await detail(ctx, root))["review"]["revision"] == 0
    monkeypatch.setattr(admin_written, "apply_review", original)
    assert (await review(ctx, root, body)).status_code == 200


@pytest.mark.parametrize(
    "broken",
    [
        "rights",
        "score",
        "explanation",
        "reference",
        "knowledge",
        "duplicate",
        "option",
        "boolean",
        "normalization",
        "accepted",
        "scoring",
        "remote_image",
        "conflict",
    ],
)
def test_incomplete_or_ambiguous_answers_cannot_be_approved(broken):
    from app.services.written_review import initial_draft

    records = make_records()
    body = approval(initial_draft(records["questions.jsonl"][0], None))
    d, flags = body["draft"], []
    if broken == "rights":
        body["rights_confirmed"] = False
    elif broken == "score":
        d["score"] = 0
    elif broken == "explanation":
        d["explanation"] = ""
    elif broken == "reference":
        d["answer_reference"] = ""
    elif broken == "knowledge":
        d["primary_knowledge_point_id"] = "missing"
    elif broken == "duplicate":
        d["secondary_knowledge_point_ids"] = [d["primary_knowledge_point_id"]]
    elif broken == "option":
        d["answer"]["option_ids"] = ["Z"]
    elif broken == "boolean":
        d.update(question_type="true_false", answer={"kind": "boolean", "value": "false"})
    elif broken == "normalization":
        d.update(
            question_type="fill_blank",
            scoring_rule="exact_text",
            answer={"kind": "text", "accepted": ["2"], "normalization": "ignore_everything"},
        )
    elif broken == "accepted":
        d.update(
            question_type="fill_blank",
            scoring_rule="exact_text",
            answer={"kind": "text", "accepted": [" "], "normalization": "exact"},
        )
    elif broken == "scoring":
        d.update(
            question_type="multiple_choice",
            scoring_rule="unconfirmed",
            answer={"kind": "options", "option_ids": ["A", "B"]},
        )
    elif broken == "remote_image":
        d["options"][0]["text"] = "![图](https://example.test/secret.png)"
    elif broken == "conflict":
        flags = ["answer_conflict"]
    request = ReviewInput.model_validate(body)
    with pytest.raises(HTTPException) as error:
        validate_approval(
            request.draft.model_dump(),
            request,
            flags,
            records["knowledge_points.json"]["nodes"],
            ["assets/test.png"],
        )
    assert error.value.status_code == 422


async def test_conflict_resolution_and_manual_unpublish_use_current_version(api_context):
    ctx = api_context
    root = await staged(ctx)
    body = approval((await detail(ctx, root, "q2"))["review"]["draft"])
    assert (await review(ctx, root, body, "q2")).status_code == 422
    body["resolutions"] = {"answer_conflict": "已对照自编算术原题和答案，手动订正为 B"}
    body["draft"]["answer"]["option_ids"] = ["B"]
    result = await review(ctx, root, body, "q2")
    assert result.status_code == 200, result.text
    pub = result.json()["review"]["publication"]
    url = f"/api/v1/admin/written-bank/published/{pub['id']}/state"
    data = {"expected_version": pub["catalog_version"], "is_active": False}
    assert (
        await ctx.client.post(url, json=data, headers=ctx.headers(ctx.users.admin))
    ).status_code == 200
    assert (
        await ctx.client.post(url, json=data, headers=ctx.headers(ctx.users.admin))
    ).status_code == 409
    data.update(expected_version=pub["catalog_version"] + 1, is_active=True)
    assert (
        await ctx.client.post(url, json=data, headers=ctx.headers(ctx.users.admin))
    ).status_code == 200
    batches = (
        await ctx.client.get(
            "/api/v1/admin/written-bank/batches", headers=ctx.headers(ctx.users.admin)
        )
    ).json()
    assert batches["items"][0]["summary"]["published_count"] == 1


async def test_independent_reviewers_serialize_and_only_one_revision_wins(
    api_context, isolated_database
):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.domain.models import User
    from app.infra.db import get_session
    from app.main import app

    ctx = api_context
    sessions = async_sessionmaker(isolated_database, expire_on_commit=False)
    async with sessions() as session:
        admin = ctx.users.admin
        session.add(
            User(id=admin.id, username=admin.username, role="admin", password_hash="unused")
        )
        await session.commit()

    async def separate_session():
        async with sessions() as session:
            yield session

    original = app.dependency_overrides[get_session]
    app.dependency_overrides[get_session] = separate_session
    try:
        root = await staged(ctx)
        body = approval((await detail(ctx, root))["review"]["draft"])
        other = {**deepcopy(body), "request_id": str(uuid4()), "note": "另一位审核者同时发布"}
        results = await asyncio.gather(review(ctx, root, body), review(ctx, root, other))
        assert sorted(r.status_code for r in results) == [200, 409]
        async with sessions() as session:
            assert (
                await session.scalar(select(func.count()).select_from(WrittenQuestionVersion)) == 1
            )
            assert (
                await session.scalar(select(func.count()).select_from(WrittenReviewRevision)) == 1
            )
    finally:
        app.dependency_overrides[get_session] = original
