"""验证公开原创包通过真实题库暂存、审核和规则判分。"""

import runpy
from pathlib import Path
from uuid import uuid4

from test_written_attempts import attempt, submit
from test_written_bank_import import preview
from test_written_review import detail, review

from app.services.written_bank_package import parse_package


def archive():
    root = Path(__file__).resolve().parents[2]
    return runpy.run_path(str(root / "tools/build_written_demo.py"))["build_archive"]()


def test_original_demo_build_is_deterministic_and_has_balanced_point_coverage():
    first = archive()
    assert first == archive()
    payload, summary, _ = parse_package(first)
    root = Path(__file__).resolve().parents[2]
    checked_in, _, _ = parse_package((root / "examples/written/oi-written-demo.zip").read_bytes())
    assert checked_in == payload
    assert summary["levels"] == {"J": 8, "S": 8}
    assert summary["questions"] == 16 and summary["knowledge_leaves"] == 8
    assert summary["groups"] == 1 and summary["published_count"] == 0
    for paper in payload["papers"]:
        questions = [q for q in payload["questions"] if q["paper_id"] == paper["id"]]
        assert sum(q["score"] for q in questions) == paper["total_score"] == 16
        assert {q["question_type"] for q in questions} == {
            "single_choice", "multiple_choice", "true_false", "fill_blank",
        }
    for node in payload["knowledge_points"]["nodes"]:
        assert sum(q["primary_knowledge_point_id"] == node["id"] for q in payload["questions"]) == 2


async def test_original_demo_publish_and_score(api_context):
    ctx = api_context
    packed = archive()
    payload, _, _ = parse_package(packed)
    batch = await preview(ctx, packed)
    root = f"/api/v1/admin/written-bank/batches/{batch['id']}"
    staged = await ctx.client.post(root + "/stage", headers=ctx.headers(ctx.users.admin))
    assert staged.status_code == 200, staged.text
    catalog = "/api/v1/written/questions"
    empty = await ctx.client.get(catalog, headers=ctx.headers())
    assert not empty.json()["items"]
    for source in payload["questions"]:
        ident = source["id"]
        current = (await detail(ctx, root, ident))["review"]
        approved = await review(ctx, root, {
            "expected_revision": current["revision"], "request_id": str(uuid4()),
            "action": "approve", "note": "校对项目原创题及解答", "rights_confirmed": True,
            "draft": {**current["draft"], "answer_reference": "核对原创题的逐步推导与参考答案"},
        }, ident)
        assert approved.status_code == 200, approved.text
        question_id = approved.json()["review"]["publication"]["id"]
        question_response = await ctx.client.get(f"{catalog}/{question_id}", headers=ctx.headers())
        assert question_response.status_code == 200
        question = question_response.json()
        expected = source["answer"]
        if expected["kind"] == "text":
            expected = {"kind": "text", "value": expected["accepted"][0]}
        if source.get("group_id"):
            assert "p[i - 1]" in question["shared_stem"]
        submitted = await submit(ctx, attempt(question, expected))
        assert submitted.status_code == 200, submitted.text
        assert submitted.json()["correct"] and submitted.json()["earned_score"] == 2
