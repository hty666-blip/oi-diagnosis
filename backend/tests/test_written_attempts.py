"""从发布到交卷验证笔试评分、记录归属与版本约束。"""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_written_review import approval, detail, review, staged

from app.domain.models import User, WrittenAttempt
from app.infra.db import get_session
from app.main import app

ATTEMPTS = "/api/v1/written/attempts"
QUESTIONS = "/api/v1/written/questions"


def single(option):
    return {"kind": "option", "option_ids": [option]}


def multiple(*options):
    return {"kind": "options", "option_ids": list(options)}


def boolean(value):
    return {"kind": "boolean", "value": value}


def fill(value):
    return {"kind": "text", "value": value}


async def publish(ctx, draft_fields=None):
    root = await staged(ctx)
    body = approval((await detail(ctx, root))["review"]["draft"])
    body["draft"].update(draft_fields or {})
    response = await review(ctx, root, body)
    assert response.status_code == 200, response.text
    publication = response.json()["review"]["publication"]
    current = await ctx.client.get(f"{QUESTIONS}/{publication['id']}", headers=ctx.headers())
    assert current.status_code == 200, current.text
    return root, body, publication, current.json()


def attempt(question, answer):
    return {
        "question_id": question["id"],
        "version_id": question["version_id"],
        "request_id": str(uuid4()),
        "response": answer,
    }


async def submit(ctx, body, user=None):
    return await ctx.client.post(ATTEMPTS, json=body, headers=ctx.headers(user))


async def history(ctx, view="history", user=None, **params):
    result = await ctx.client.get(
        ATTEMPTS, params={"view": view, **params}, headers=ctx.headers(user)
    )
    assert result.status_code == 200, result.text
    return result.json()


async def test_catalog_search_uses_full_current_stem_and_reviewed_attributes(api_context):
    ctx = api_context
    _, _, publication, _ = await publish(ctx, {"stem": "阅读下面的材料。" * 30 + "关键片段 80%"})
    found = await ctx.client.get(
        QUESTIONS,
        params={"q": "关键片段 80%", "level": "J", "question_type": "single_choice"},
        headers=ctx.headers(),
    )
    assert found.status_code == 200
    assert [row["id"] for row in found.json()["items"]] == [publication["id"]]
    for filters in ({"level": "S"}, {"question_type": "fill_blank"}, {"q": "关键片段 80_"}):
        rejected = await ctx.client.get(QUESTIONS, params=filters, headers=ctx.headers())
        assert rejected.status_code == 200 and rejected.json()["items"] == []


@pytest.mark.parametrize(
    ("draft_fields", "correct_answer", "wrong_answer", "invalid_answer"),
    [
        pytest.param({}, single("A"), single("B"), single("Z"), id="single"),
        pytest.param(
            {
                "question_type": "multiple_choice",
                "options": [
                    {"id": "A", "text": "第一项"},
                    {"id": "B", "text": "第二项"},
                    {"id": "C", "text": "第三项"},
                ],
                "answer": multiple("A", "C"),
            },
            multiple("C", "A"),
            multiple("A"),
            multiple("A", "A"),
            id="multiple_unordered_set",
        ),
        pytest.param(
            {"question_type": "true_false", "answer": boolean(False), "options": []},
            boolean(False),
            boolean(True),
            boolean("false"),
            id="strict_boolean",
        ),
        pytest.param(
            {
                "question_type": "fill_blank",
                "scoring_rule": "exact_text",
                "options": [],
                "answer": {
                    "kind": "text",
                    "accepted": ["A B", " C "],
                    "normalization": "exact",
                },
            },
            fill(" C "),
            fill("C"),
            fill(" \t"),
            id="fill_exact_preserves_whitespace",
        ),
        pytest.param(
            {
                "question_type": "fill_blank",
                "scoring_rule": "exact_text",
                "options": [],
                "answer": {
                    "kind": "text",
                    "accepted": [" A B "],
                    "normalization": "trim_outer_whitespace",
                },
            },
            fill("\tA B \n"),
            fill("A  B"),
            boolean(True),
            id="fill_trim_only_outer_whitespace",
        ),
    ],
)
async def test_rules_use_published_answer_and_reject_invalid_responses(
    api_context, draft_fields, correct_answer, wrong_answer, invalid_answer
):
    ctx = api_context
    _, approval_body, _, question = await publish(ctx, {**draft_fields, "score": 2.5})
    catalog = await ctx.client.get(QUESTIONS, headers=ctx.headers())
    expected = approval_body["draft"]
    for part in [question, *catalog.json()["items"]]:
        assert {"answer", "explanation", "answer_reference", "review_note"}.isdisjoint(part)
        assert part["score"] == 2.5
    for answer, correct, score in [(correct_answer, True, 2.5), (wrong_answer, False, 0)]:
        body = attempt(question, answer)
        result = await submit(ctx, body)
        assert result.status_code == 200, result.text
        graded = result.json()
        assert graded["correct"] is correct
        assert graded["earned_score"] == score and graded["max_score"] == 2.5
        assert graded["version_id"] == question["version_id"]
        assert graded["answer"] == expected["answer"]
        assert graded["explanation"] == expected["explanation"]
        assert {"review_note", "answer_reference", "source_locator"}.isdisjoint(graded)
        if answer["kind"] == "options" and correct:
            reordered = {**body, "response": multiple(*reversed(answer["option_ids"]))}
            replay = await submit(ctx, reordered)
            assert replay.status_code == 200 and replay.json() == {**graded, "replayed": True}
        if answer["kind"] == "text":
            assert graded["response"] == answer
    rejected = await submit(ctx, attempt(question, invalid_answer))
    assert rejected.status_code == 422, rejected.text
    result = await history(ctx)
    assert len(result["items"]) == 2
    assert all({"answer", "explanation", "response"}.isdisjoint(item) for item in result["items"])


async def test_retry_before_visibility_check_preserves_history_after_republish_and_unpublish(
    api_context,
):
    ctx = api_context
    root, initial, _, original = await publish(ctx)
    body = attempt(original, single("B"))
    first = await submit(ctx, body)
    assert first.status_code == 200, first.text
    old = first.json()
    assert old["correct"] is False and old["replayed"] is False

    next_review = {
        **initial,
        "request_id": str(uuid4()),
        "expected_revision": 1,
        "note": "修订答案、分值和题干",
        "draft": {
            **initial["draft"],
            "stem": "改题：三减零等于几？",
            "score": 4.0,
            "answer": single("B"),
            "explanation": "三减去零仍是三，因此选择选项 B。",
        },
    }
    approved = await review(ctx, root, next_review)
    assert approved.status_code == 200, approved.text
    new = (await ctx.client.get(f"{QUESTIONS}/{original['id']}", headers=ctx.headers())).json()
    assert new["version_id"] != original["version_id"] and new["revision"] == 2
    stale = await submit(ctx, attempt(original, single("B")))
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "written_version_changed"
    protected = await submit(ctx, {**body, "version_id": new["version_id"]})
    assert protected.status_code == 409
    assert protected.json()["detail"]["code"] == "written_request_conflict"

    new_result = await submit(ctx, attempt(new, single("B")))
    assert new_result.status_code == 200, new_result.text
    assert new_result.json()["earned_score"] == 4.0
    assert not (await history(ctx, "wrong"))["items"]
    corrected = await history(ctx, "corrected")
    assert [item["id"] for item in corrected["items"]] == [new_result.json()["id"]]

    pub = approved.json()["review"]["publication"]
    unpublish = await ctx.client.post(
        f"/api/v1/admin/written-bank/published/{pub['id']}/state",
        json={"expected_version": pub["catalog_version"], "is_active": False},
        headers=ctx.headers(ctx.users.admin),
    )
    assert unpublish.status_code == 200, unpublish.text
    unavailable = await submit(ctx, attempt(new, single("B")))
    assert unavailable.status_code == 409
    assert unavailable.json()["detail"]["code"] == "written_question_unavailable"
    replay = await submit(ctx, body)
    assert replay.status_code == 200 and replay.json() == {**old, "replayed": True}
    history_row = await ctx.client.get(f"{ATTEMPTS}/{old['id']}", headers=ctx.headers())
    assert history_row.status_code == 200
    assert history_row.json()["question"]["stem"] == original["stem"]
    assert history_row.json()["answer"] == single("A")
    assert history_row.json()["explanation"] == initial["draft"]["explanation"]
    assert history_row.json()["max_score"] == 2.0
    items = (await history(ctx))["items"]
    assert len(items) == 2 and all(not item["is_available"] for item in items)


async def test_wrong_book_tracks_latest_attempt_not_any_past_wrong_response(api_context):
    ctx = api_context
    _, _, _, question = await publish(ctx)
    responses = []
    for answer in ["A", "B", "B", "A", "B"]:
        result = await submit(ctx, attempt(question, single(answer)))
        assert result.status_code == 200, result.text
        responses.append(result.json()["id"])
        wrong = await history(ctx, "wrong")
        corrected = await history(ctx, "corrected")
        if answer == "B":
            assert [item["id"] for item in wrong["items"]] == responses[-1:]
            assert not corrected["items"]
        else:
            assert not wrong["items"]
            assert [item["id"] for item in corrected["items"]] == (
                [] if len(responses) == 1 else responses[-1:]
            )
    assert [item["id"] for item in (await history(ctx))["items"]] == responses[::-1]
    earlier = await history(ctx, before_id=responses[-2])
    assert [item["id"] for item in earlier["items"]] == responses[:-2][::-1]
    assert not (await history(ctx, "wrong", before_id=responses[-1]))["items"]


async def test_attempt_details_images_and_idempotency_are_scoped_to_student(api_context):
    ctx = api_context
    _, _, _, question = await publish(ctx)
    body = attempt(question, single("B"))
    first = await submit(ctx, body)
    assert first.status_code == 200, first.text
    submitted = first.json()
    attempt_url = f"{ATTEMPTS}/{submitted['id']}"
    image_url = f"{attempt_url}/assets/0"
    image = await ctx.client.get(image_url, headers=ctx.headers())
    assert image.status_code == 200 and image.headers["Cache-Control"] == "no-store"

    peer_headers = ctx.headers(ctx.users.peer)
    assert (await ctx.client.get(attempt_url, headers=peer_headers)).status_code == 404
    assert (await ctx.client.get(image_url, headers=peer_headers)).status_code == 404
    assert (await history(ctx, user=ctx.users.peer))["items"] == []
    peer = await submit(ctx, {**body, "response": single("A")}, ctx.users.peer)
    assert peer.status_code == 200 and peer.json()["id"] != submitted["id"]
    assert [item["id"] for item in (await history(ctx, "wrong"))["items"]] == [submitted["id"]]
    assert not (await history(ctx, "wrong", ctx.users.peer))["items"]
    for user in [ctx.users.teacher, ctx.users.admin]:
        assert (await submit(ctx, body, user)).status_code == 403
        for path in [ATTEMPTS, attempt_url, image_url]:
            assert (await ctx.client.get(path, headers=ctx.headers(user))).status_code == 403
    assert (await ctx.client.get(attempt_url)).status_code == 401


async def test_concurrent_duplicate_http_requests_commit_one_attempt(
    api_context, isolated_database
):
    ctx = api_context
    sessions = async_sessionmaker(isolated_database, expire_on_commit=False)
    async with sessions() as session:
        for user in [ctx.users.admin, ctx.users.student]:
            session.add(
                User(id=user.id, username=user.username, role=user.role, password_hash="unused")
            )
        await session.commit()

    async def separate_session():
        async with sessions() as session:
            yield session

    original = app.dependency_overrides[get_session]
    app.dependency_overrides[get_session] = separate_session
    try:
        _, _, _, question = await publish(ctx)
        body = attempt(question, single("A"))
        left, right = await asyncio.gather(submit(ctx, body), submit(ctx, body))
        assert left.status_code == right.status_code == 200
        left_row, right_row = left.json(), right.json()
        assert left_row["id"] == right_row["id"]
        assert sorted([left_row["replayed"], right_row["replayed"]]) == [False, True]
        assert (await submit(ctx, {**body, "response": single("B")})).status_code == 409
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(WrittenAttempt)) == 1
    finally:
        app.dependency_overrides[get_session] = original
