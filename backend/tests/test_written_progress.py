"""接真实发布和作答，验证学习路线对版本、重复题和学员的约束。"""

from copy import deepcopy
from uuid import uuid4

from test_written_attempts import attempt, single, submit
from test_written_bank_import import make_records
from test_written_review import approval, detail, review, staged

from app.domain.models import WrittenQuestionVersion

PROGRESS = "/api/v1/written/progress"
POINT = "KP-08-02-02"


async def publish(ctx, root, ident, **changes):
    data = await detail(ctx, root, ident)
    body = approval(data["review"]["draft"])
    body["expected_revision"] = data["review"]["revision"]
    body["draft"].update(changes)
    body["resolutions"]["answer_conflict"] = "已将原创例题的选项与答案重新逐项核对。"
    result = await review(ctx, root, body, ident)
    assert result.status_code == 200, result.text
    publication = result.json()["review"]["publication"]
    current = await ctx.client.get(
        f"/api/v1/written/questions/{publication['id']}", headers=ctx.headers()
    )
    assert current.status_code == 200, current.text
    return {**publication, "version_id": current.json()["version_id"]}


async def answer(ctx, question, option):
    response = await submit(ctx, attempt(question, single(option)))
    assert response.status_code == 200, response.text
    return response.json()["id"]


async def progress(ctx, user=None):
    result = await ctx.client.get(PROGRESS, headers=ctx.headers(user))
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "no-store"
    return result.json()["items"]


async def test_requires_first_correct_answer_to_a_different_exercise(api_context):
    ctx = api_context
    records = make_records()
    nodes = records["knowledge_points.json"]["nodes"]
    nodes[0]["prerequisites"] = ["BASICS"]
    nodes.append({"id": "BASICS", "name": "输入规模", "is_leaf": True})
    first = records["questions.jsonl"][0]
    records["questions.jsonl"].insert(
        1, {**deepcopy(first), "id": "duplicate", "relation_to_canonical": "exact_duplicate"}
    )
    root = await staged(ctx, records)
    assert not await progress(ctx)
    first = await publish(ctx, root, "q1")
    duplicate = await publish(ctx, root, "duplicate")
    new = await publish(ctx, root, "q2")
    error_id = await answer(ctx, first, "B")
    await answer(ctx, first, "A")
    await answer(ctx, duplicate, "A")
    [row] = await progress(ctx)
    assert row["point"]["id"] == POINT
    assert row["point"]["prerequisites"] == ["输入规模"]
    assert row["status"] == "needs_review" and row["last_error_id"] == error_id
    assert row["attempt_count"] == 3 and row["next_question"] == {
        "id": new["id"], "version_id": new["version_id"],
    }
    assert not await progress(ctx, ctx.users.peer)
    for user in (ctx.users.teacher, ctx.users.admin):
        assert (await ctx.client.get(PROGRESS, headers=ctx.headers(user))).status_code == 403

    verified = await answer(ctx, new, "A")
    [row] = await progress(ctx)
    assert row["status"] == "reviewed" and row["review_attempt_id"] == verified
    assert row["next_question"] is None and row["attempt_count"] == 4
    new_error = await answer(ctx, new, "B")
    await answer(ctx, new, "A")
    [row] = await progress(ctx)
    assert row["status"] == "needs_review" and row["last_error_id"] == new_error
    assert row["review_attempt_id"] is None and row["next_question"] is None


async def test_legacy_snapshot_later_retag_and_catalog_visibility(api_context):
    ctx = api_context
    records = make_records()
    records["knowledge_points.json"]["nodes"].append(
        {"id": "TIME", "name": "时间复杂度", "is_leaf": True}
    )
    root = await staged(ctx, records)
    first = await publish(ctx, root, "q1")
    new = await publish(ctx, root, "q2")
    for publication in (first, new):
        row = await ctx.session.get(WrittenQuestionVersion, publication["version_id"])
        row.snapshot_metadata = {
            key: value for key, value in row.snapshot_metadata.items() if key != "learning_topic"
        }
    await ctx.session.flush()
    await answer(ctx, first, "B")
    await publish(ctx, root, "q1", primary_knowledge_point_id="TIME")
    [row] = await progress(ctx)
    assert row["point"]["name"] == "空间复杂度" and row["next_question"]["id"] == new["id"]

    await publish(ctx, root, "q2", level="S")
    [row] = await progress(ctx)
    assert row["next_question"] is None
    new = await publish(ctx, root, "q2", level="J")
    state = await ctx.client.post(
        f"/api/v1/admin/written-bank/published/{new['id']}/state",
        headers=ctx.headers(ctx.users.admin),
        json={"expected_version": new["catalog_version"], "is_active": False},
    )
    assert state.status_code == 200, state.text
    [row] = await progress(ctx)
    assert row["next_question"] is None


async def test_previous_attempt_outside_recent_window_cannot_verify_review(api_context, monkeypatch):
    from app.api.v1 import written_progress

    monkeypatch.setattr(written_progress, "RECENT_LIMIT", 2)
    ctx = api_context
    root = await staged(ctx)
    first = await publish(ctx, root, "q1")
    second = await publish(ctx, root, "q2")
    for question, option in ((second, "A"), (first, "B"), (second, "A")):
        await answer(ctx, question, option)
    other = make_records()
    other["questions.jsonl"][0]["stem"] += str(uuid4())
    await publish(ctx, await staged(ctx, other), "q1")
    [row] = await progress(ctx)
    assert row["status"] == "needs_review" and row["attempt_count"] == 2
    assert row["next_question"] is None
