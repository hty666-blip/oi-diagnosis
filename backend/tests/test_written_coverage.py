"""管理员覆盖统计跟随审核版本，并可按来源主知识点定位待审题。"""

from test_written_bank_import import make_records
from test_written_knowledge import ARTICLE, POINT, create_entry, edit
from test_written_progress import publish
from test_written_review import staged

from app.domain.models import WrittenQuestionVersion


async def test_coverage_preserves_gaps_and_counts_live_reviewed_tags(api_context):
    ctx = api_context
    records = make_records()
    records["knowledge_points.json"]["nodes"].append(
        {"id": "TIME", "name": "时间复杂度", "is_leaf": True}
    )
    first, second = records["questions.jsonl"]
    second["level"] = None
    records["questions.jsonl"].append({
        **first, "id": "unclassified", "stem": "未标注主知识点的候选题",
        "canonical_question_id": "unclassified", "primary_knowledge_point_id": None,
    })
    root = await staged(ctx, records)
    headers = ctx.headers(ctx.users.admin)
    url = root + "/coverage"
    assert (await ctx.client.get(url, headers=ctx.headers())).status_code == 403
    assert (await ctx.client.get(url, headers=ctx.headers(ctx.users.teacher))).status_code == 403

    async def coverage():
        result = await ctx.client.get(url, headers=headers)
        assert result.status_code == 200, result.text
        assert result.headers["cache-control"] == "no-store"
        data = result.json()
        assert data["unmapped_pending"] == 1
        return {entry["id"]: entry for entry in data["items"]}

    before = await coverage()
    assert before[POINT]["pending_j"] == 1 and before[POINT]["pending_unclassified"] == 1
    assert before["TIME"]["pending_j"] == before["TIME"]["published_s"] == 0
    assert before[POINT]["article_revision"] is None
    edition = await create_entry(ctx, root.split("/")[-1])
    assert (await edit(ctx, edition, "publish", ARTICLE)).status_code == 200
    publication = await publish(ctx, root, "q1", primary_knowledge_point_id="TIME", level="S")
    after = await coverage()
    assert after[POINT]["pending_j"] == after[POINT]["published_j"] == 0
    assert after["TIME"]["published_s"] == 1
    assert after[POINT]["article_revision"] == 1
    filtered = await ctx.client.get(root + "/questions", headers=headers, params={
        "point_id": POINT, "state": "needs_review",
    })
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["external_id"] == "q2"

    version = await ctx.session.get(WrittenQuestionVersion, publication["version_id"])
    version.snapshot_metadata = {k: v for k, v in version.snapshot_metadata.items() if k != "learning_topic"}
    await ctx.session.flush()
    assert (await coverage())["TIME"]["published_s"] == 1
    hidden = await ctx.client.post(
        f"/api/v1/admin/written-bank/published/{publication['id']}/state",
        headers=headers,
        json={"expected_version": publication["catalog_version"], "is_active": False},
    )
    assert hidden.status_code == 200
    assert (await coverage())["TIME"]["published_s"] == 0
