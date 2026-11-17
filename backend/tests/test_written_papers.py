"""试卷保留原始顺序，列表只显示有上架题的批次和试卷。"""

from test_written_bank_import import make_records
from test_written_progress import publish
from test_written_review import staged

PAPERS = "/api/v1/written/papers"


async def test_paper_navigation_preserves_source_order_and_batch_scope(api_context):
    ctx = api_context
    records = make_records()
    records["papers.jsonl"][0].update(level="J", year=2025, source_kind="original_demo")
    records["questions.jsonl"][0]["original_number"] = "2"
    records["questions.jsonl"][1]["original_number"] = "9-1"
    root = await staged(ctx, records)
    batch = root.split("/")[-1]
    path = f"{PAPERS}/{batch}/paper1"
    headers = ctx.headers()
    assert (await ctx.client.get(PAPERS, headers=headers)).json()["items"] == []
    assert (await ctx.client.get(path, headers=headers)).status_code == 404

    second = await publish(ctx, root, "q2")
    other_records = make_records()
    other_records["questions.jsonl"][0]["stem"] += "另一题包"
    other_root = await staged(ctx, other_records)
    await publish(ctx, other_root, "q1")

    catalog = await ctx.client.get(PAPERS, headers=headers)
    assert catalog.status_code == 200 and catalog.headers["cache-control"] == "no-store"
    rows = catalog.json()["items"]
    assert len(rows) == 2
    current = next(row for row in rows if row["batch_id"] == batch)
    assert current["source_kind"] == "original_demo" and current["year"] == 2025
    assert current["published_count"] == 1 and current["candidate_count"] == 2
    result = (await ctx.client.get(path, headers=headers)).json()
    assert [item["number"] for item in result["items"]] == ["9-1"]

    first = await publish(ctx, root, "q1")
    result = (await ctx.client.get(path, headers=headers)).json()
    assert result["paper"]["published_count"] == 2
    assert [item["id"] for item in result["items"]] == [first["id"], second["id"]]
    assert [item["number"] for item in result["items"]] == ["2", "9-1"]
    assert all("answer" not in item and "explanation" not in item for item in result["items"])
    hidden = await ctx.client.post(
        f"/api/v1/admin/written-bank/published/{second['id']}/state",
        json={"expected_version": second["catalog_version"], "is_active": False},
        headers=ctx.headers(ctx.users.admin),
    )
    assert hidden.status_code == 200
    page = (await ctx.client.get(path, headers=headers)).json()
    assert page["paper"]["published_count"] == 1
    assert page["items"][0]["version_id"] == first["version_id"]
