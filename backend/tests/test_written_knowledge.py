"""验证审核发布、草稿隔离和答疑引用版本。"""

from test_written_attempts import publish
from test_written_bank_import import make_records
from test_written_help import request_body, wrong_attempt
from test_written_progress import publish as publish_candidate
from test_written_review import staged

from app.domain.models import WrittenQuestionVersion

ADMIN = "/api/v1/admin/written-knowledge"
PUBLIC = "/api/v1/written/knowledge"
POINT = "KP-08-02-02"
ARTICLE = {
    "title": "空间复杂度中的额外数组",
    "body": "长度为 n 的额外数组占用与 n 成正比的附加空间。只使用固定数量的变量时，附加空间为常数。",
    "source": "本项目原创空间复杂度讲义第 1 节",
}


async def create_entry(ctx, batch_id, article=ARTICLE):
    response = await ctx.client.post(
        ADMIN,
        headers=ctx.headers(ctx.users.admin),
        json={"batch_id": batch_id, "point_id": POINT, "article": article},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def edit(ctx, entry, action, article=None, note="已复核定义与示例"):
    data = {"expected_version": entry["edit_version"], "action": action, "review_note": note}
    if article is not None:
        data["article"] = article
    return await ctx.client.post(
        f"{ADMIN}/{entry['id']}", headers=ctx.headers(ctx.users.admin), json=data
    )


async def test_only_published_versions_are_visible_and_draft_edits_do_not_replace_them(api_context):
    ctx = api_context
    root = await staged(ctx)
    batch = root.split("/")[-1]
    topics = await ctx.client.get(
        f"{ADMIN}/topics", params={"batch_id": batch}, headers=ctx.headers(ctx.users.admin)
    )
    assert topics.status_code == 200
    assert any(item["id"] == POINT for item in topics.json()["items"])
    assert (
        await ctx.client.get(f"{ADMIN}/topics", params={"batch_id": batch}, headers=ctx.headers())
    ).status_code == 403
    invalid = await ctx.client.post(
        ADMIN,
        headers=ctx.headers(ctx.users.admin),
        json={"batch_id": batch, "point_id": "not-in-this-package", "article": ARTICLE},
    )
    assert invalid.status_code == 422
    entry = await create_entry(ctx, batch)
    ident = entry["id"]
    duplicate = await ctx.client.post(
        ADMIN,
        headers=ctx.headers(ctx.users.admin),
        json={"batch_id": batch, "point_id": POINT, "article": ARTICLE},
    )
    assert duplicate.status_code == 409
    assert (await ctx.client.get(f"{PUBLIC}/{ident}", headers=ctx.headers())).status_code == 404
    assert (await edit(ctx, entry, "publish", {**ARTICLE, "source": ""})).status_code == 422
    published = await edit(ctx, entry, "publish", ARTICLE)
    assert published.status_code == 200, published.text
    assert published.json()["published_revision"] == 1
    stale = await edit(ctx, entry, "save", ARTICLE)
    assert stale.status_code == 409
    edition2 = {
        **ARTICLE,
        "title": "空间复杂度新讲义",
        "body": ARTICLE["body"] + "还需要区分输入本身与辅助空间。",
    }
    saved = await edit(ctx, published.json(), "save", edition2)
    assert saved.status_code == 200
    page = await ctx.client.get(PUBLIC, params={"q": "额外数组"}, headers=ctx.headers())
    assert page.status_code == 200 and page.json()["items"][0]["title"] == ARTICLE["title"]
    student = (await ctx.client.get(f"{PUBLIC}/{ident}", headers=ctx.headers())).json()
    assert student["body"] == ARTICLE["body"] and student["revision"] == 1
    assert "edit_version" not in student and "review_note" not in student
    second = await edit(ctx, saved.json(), "publish", edition2)
    assert second.status_code == 200 and second.json()["published_revision"] == 2
    old = await ctx.client.get(f"{ADMIN}/{ident}/versions/1", headers=ctx.headers(ctx.users.admin))
    assert old.json()["body"] == ARTICLE["body"]
    assert (
        await ctx.client.get(f"{ADMIN}/{ident}/versions/1", headers=ctx.headers())
    ).status_code == 403
    assert (await edit(ctx, second.json(), "unpublish")).status_code == 200
    assert (await ctx.client.get(f"{PUBLIC}/{ident}", headers=ctx.headers())).status_code == 404


async def test_help_cites_same_batch_and_preserves_edition_after_unpublish(api_context):
    ctx = api_context
    root, _, _, question = await publish(
        ctx, {"stem": "若算法额外分配长度 n 的数组，空间复杂度如何变化？"}
    )
    batch = root.split("/")[-1]
    other = make_records()
    other["questions.jsonl"][0]["stem"] = "另一个来源的题面，不可共享目录编号。"
    wrong_batch = (await staged(ctx, other)).split("/")[-1]
    elsewhere = await create_entry(ctx, wrong_batch, {**ARTICLE, "title": "其他题包同编号条目"})
    assert (await edit(ctx, elsewhere, "publish", elsewhere["article"])).status_code == 200
    url = await wrong_attempt(ctx, question)
    first = (await ctx.client.post(url, json=request_body(), headers=ctx.headers())).json()
    assert first["knowledge"] is None
    current = await create_entry(ctx, batch)
    published = (await edit(ctx, current, "publish", ARTICLE)).json()
    citation = (
        await ctx.client.post(
            url, headers=ctx.headers(), json=request_body("空间复杂度怎么判断？", first["id"])
        )
    ).json()
    assert citation["knowledge"]["entry_id"] == current["id"]
    assert citation["knowledge"]["revision"] == 1
    assert citation["knowledge"]["body"] == ARTICLE["body"]
    assert (await edit(ctx, published, "unpublish")).status_code == 200
    after = (
        await ctx.client.post(
            url, headers=ctx.headers(), json=request_body("再讲一下。", citation["id"])
        )
    ).json()
    assert after["knowledge"] is None
    history = (await ctx.client.get(url, headers=ctx.headers())).json()["items"]
    assert history[1]["knowledge"] == citation["knowledge"]


async def test_article_practice_stays_in_batch_and_follows_current_reviewed_topic(api_context):
    ctx = api_context
    records = make_records()
    records["knowledge_points.json"]["nodes"].append(
        {"id": "TIME", "name": "时间复杂度", "is_leaf": True}
    )
    root = await staged(ctx, records)
    entry = await create_entry(ctx, root.split("/")[-1])
    assert (await edit(ctx, entry, "publish", ARTICLE)).status_code == 200
    first = await publish_candidate(ctx, root, "q1")
    version = await ctx.session.get(WrittenQuestionVersion, first["version_id"])
    version.snapshot_metadata = {k: v for k, v in version.snapshot_metadata.items() if k != "learning_topic"}
    await ctx.session.flush()
    records["questions.jsonl"][0]["stem"] += "其他批次"
    await publish_candidate(ctx, await staged(ctx, records), "q1")

    async def linked():
        response = await ctx.client.get(f"{PUBLIC}/{entry['id']}", headers=ctx.headers())
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        return response.json()["related_questions"]

    [one] = await linked()
    assert one["id"] == first["id"] and one["version_id"] == first["version_id"]
    assert "answer" not in one and "explanation" not in one
    await publish_candidate(ctx, root, "q1", primary_knowledge_point_id="TIME")
    assert not await linked()
    next_question = await publish_candidate(ctx, root, "q2")
    [one] = await linked()
    assert one["id"] == next_question["id"]
    state = await ctx.client.post(
        f"/api/v1/admin/written-bank/published/{one['id']}/state",
        json={"expected_version": next_question["catalog_version"], "is_active": False},
        headers=ctx.headers(ctx.users.admin),
    )
    assert state.status_code == 200, state.text
    assert not await linked()
