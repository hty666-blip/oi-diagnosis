"""真实专项从简答发布到部分给分，覆盖权限和规则判分边界。"""

from uuid import uuid4

import pytest
from test_written_assignments import ASSIGNMENTS, groups, send, task, task_body
from test_written_attempts import attempt, submit
from test_written_bank_import import make_records
from test_written_progress import progress, publish
from test_written_review import approval, detail, review, staged

from app.api.v1 import written_short_grades

RUBRIC = {
    "kind": "rubric",
    "reference": "二叉树每层的节点数至多是上一层的两倍，故第 5 层至多 16 个。",
    "rubric": [{"criterion": "指出每个节点至多两个子节点", "points": 2},
               {"criterion": "正确推导第五层至多十六个节点", "points": 2}],
}


async def test_short_answer_waits_for_scoped_teacher_and_uses_frozen_rubric(api_context):
    ctx = api_context
    group, _ = await groups(ctx)
    records = make_records()
    records["papers.jsonl"][0].update(total_score=6, duration_minutes=30)
    records["questions.jsonl"][0].update(
        stem="为什么二叉树的第五层至多有 16 个节点？", question_type="short_answer",
        answer=RUBRIC, options=[], score=4, scoring_rule="teacher_rubric",
    )
    records["questions.jsonl"][1].update(score=2)
    source = await staged(ctx, records)
    pending = await detail(ctx, source, "q1")
    invalid = approval(pending["review"]["draft"])
    invalid["draft"].update(score=5, scoring_rule="teacher_rubric")
    invalid["resolutions"]["answer_conflict"] = "已逐条核对原创简答题的评分依据。"
    assert (await review(ctx, source, invalid, "q1")).status_code == 422
    original = await publish(ctx, source, "q1", score=4, scoring_rule="teacher_rubric")
    await publish(ctx, source, "q2")
    batch = source.split("/")[-1]
    assert not (await task(ctx, f"/api/v1/written/papers/{batch}/paper1"))["paper"]["mock_available"]

    assignment = await send(ctx, task_body(group, original))
    assert assignment.status_code == 200, assignment.text
    ident = assignment.json()["id"]
    path = f"{ASSIGNMENTS}/{ident}"
    response = {"kind": "text", "value": "二叉树每个节点至多有两个子节点。"}
    body = attempt(original, response)
    assert (await submit(ctx, body)).status_code == 409
    body["assignment_id"] = ident
    accepted = await submit(ctx, body)
    assert accepted.status_code == 200, accepted.text
    saved = accepted.json()
    assert saved["correct"] is None and saved["earned_score"] is None and saved["answer"] is None
    assert (await submit(ctx, body)).json()["id"] == saved["id"]
    assert (await submit(ctx, {**body, "request_id": str(uuid4())})).status_code == 409
    assert not await progress(ctx)

    matrix = await task(ctx, path, ctx.users.teacher)
    assert matrix["rows"][0]["submitted_count"] == 1 and matrix["rows"][0]["correct_count"] == 0
    assert matrix["rows"][0]["cells"][0]["correct"] is None
    grader = f"{path}/short-reviews/{saved['id']}"
    assert (await ctx.client.get(grader, headers=ctx.headers(ctx.users.other_teacher))).status_code == 404
    assert (await ctx.client.get(grader, headers=ctx.headers())).status_code == 403

    newer_answer = {**RUBRIC, "rubric": [{"criterion": "新版采用单点评分要点", "points": 4}]}
    await publish(ctx, source, "q1", answer=newer_answer, score=4, scoring_rule="teacher_rubric")
    open_grade = await task(ctx, grader, ctx.users.teacher)
    assert open_grade["answer"] == RUBRIC and open_grade["response"] == response["value"]
    assert open_grade["question"]["version_id"] == original["version_id"]
    feedback = {"points": [2, 0], "feedback": "还需要逐层推导第五层的节点上限。"}
    invalid_score = await ctx.client.post(grader, json={**feedback, "points": [3, 1]},
                                          headers=ctx.headers(ctx.users.teacher))
    assert invalid_score.status_code == 422
    final = await ctx.client.post(grader, json=feedback, headers=ctx.headers(ctx.users.teacher))
    assert final.status_code == 200, final.text
    assert (await ctx.client.post(grader, json=feedback, headers=ctx.headers(ctx.users.teacher))).json() == final.json()
    assert (await ctx.client.post(grader, json={**feedback, "points": [2, 2]},
                                  headers=ctx.headers(ctx.users.teacher))).status_code == 409
    receipt = await task(ctx, f"/api/v1/written/attempts/{saved['id']}")
    assert receipt["teacher_grade"] == final.json() and receipt["earned_score"] == 2 and not receipt["correct"]
    assert receipt["answer"] == RUBRIC
    assert not (await task(ctx, "/api/v1/written/attempts?view=wrong"))["items"]


@pytest.mark.parametrize("outcome", ["accepted", "unfounded", "disagreed"])
async def test_draft_requires_grounded_points_and_two_roles_and_never_posts_grades(api_context, monkeypatch, outcome):
    ctx = api_context
    group, _ = await groups(ctx)
    records = make_records()
    records["questions.jsonl"][0].update(
        question_type="short_answer", answer=RUBRIC, score=4, options=[],
        asset_paths=[], scoring_rule="teacher_rubric",
    )
    source = await staged(ctx, records)
    question = await publish(ctx, source, "q1", score=4, scoring_rule="teacher_rubric")
    assignment = await send(ctx, task_body(group, question))
    assert assignment.status_code == 200, assignment.text
    ident = assignment.json()["id"]
    phrase = "每个节点至多有两个子节点"
    received = await submit(ctx, {**attempt(question, {"kind": "text", "value": phrase}), "assignment_id": ident})
    assert received.status_code == 200, received.text
    attempt_id = received.json()["id"]
    review_url = f"{ASSIGNMENTS}/{ident}/short-reviews/{attempt_id}"
    route = review_url + "/suggestion"
    author = ctx.headers(ctx.users.teacher)
    assert (await ctx.client.post(route, headers=author)).status_code == 503

    class Model:
        available = True
        calls = []

        async def chat_json(self, system, user, **kwargs):
            import json

            self.calls.append((system, json.loads(user)))
            if len(self.calls) == 1:
                return {"awards": [{"points": 2, "quote": "从未写过" if outcome == "unfounded" else phrase,
                                    "reason": "准确说明了每个节点的分支上限。"},
                                   {"points": 0, "quote": "", "reason": "未推导第五层节点数。"}]}
            return {"accepted": outcome != "disagreed", "review_note": "已核对给分和引用。"}

    model = Model()

    async def client(_):
        return model

    monkeypatch.setattr(written_short_grades, "runtime_client", client)
    assert (await ctx.client.post(route, headers=ctx.headers(ctx.users.other_teacher))).status_code == 404
    suggested = await ctx.client.post(route, headers=author)
    again = await ctx.client.post(route, headers=author)
    if outcome == "accepted":
        assert suggested.status_code == 200, suggested.text
        assert again.json() == suggested.json()
        assert (await task(ctx, review_url, ctx.users.teacher))["suggestion"] == suggested.json()
    else:
        assert suggested.status_code == 503 and again.status_code == 409
        assert (await task(ctx, review_url, ctx.users.teacher))["suggestion"] is None
    assert len(model.calls) == (1 if outcome == "unfounded" else 2)
    if len(model.calls) == 2:
        assert model.calls[0][0] != model.calls[1][0]
        assert model.calls[1][1]["proposal"]["awards"][0]["quote"] == phrase
    assert set(model.calls[0][1]) == {"stem", "shared_stem", "reference", "rubric", "student_answer"}
    student = await task(ctx, f"/api/v1/written/attempts/{attempt_id}")
    assert student["earned_score"] is None and student["answer"] is None and "suggestion" not in student
    assert (await ctx.client.post(review_url, headers=author, json={"points": [1, 0], "feedback": "人工确认部分证据。"})).status_code == 200
    assert (await ctx.client.post(route, headers=author)).status_code == 409
    assert (await task(ctx, f"/api/v1/written/attempts/{attempt_id}"))["earned_score"] == 1
