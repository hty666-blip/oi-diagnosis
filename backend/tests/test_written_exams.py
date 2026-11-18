"""整卷模拟的时间、固定题面、草稿冲突和不可重复交卷。"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select
from test_written_attempts import attempt, single
from test_written_bank_import import make_records
from test_written_progress import publish
from test_written_review import staged

from app.api.v1 import written_exams
from app.domain.models import WrittenAttempt

EXAMS = "/api/v1/written/exams"


async def test_mock_exam_recovers_draft_and_grades_the_frozen_paper_after_deadline(api_context, monkeypatch):
    ctx = api_context
    clock = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(written_exams, "now", lambda: clock)
    records = make_records()
    records["papers.jsonl"][0].update(duration_minutes=10, total_score=4)
    records["questions.jsonl"][0].update(original_number="2", score=2)
    records["questions.jsonl"][1].update(original_number="9-1", score=2)
    root = await staged(ctx, records)
    start = f"{EXAMS}/start/{root.split('/')[-1]}/paper1"
    headers = ctx.headers()
    first = await publish(ctx, root, "q1")
    assert (await ctx.client.post(start, headers=headers)).status_code == 409
    second = await publish(ctx, root, "q2")

    begun = await ctx.client.post(start, headers=headers)
    assert begun.status_code == 200, begun.text
    exam = begun.json()
    assert [item["number"] for item in exam["items"]] == ["2", "9-1"]
    assert exam["status"] == "working" and exam["max_score"] == 4 and exam["earned_score"] is None
    assert datetime.fromisoformat(exam["deadline"]) == clock + timedelta(minutes=10)
    exam_url = f"{EXAMS}/{exam['id']}"
    question_url = f"{exam_url}/questions/{first['id']}"
    for url in [exam_url, question_url, question_url + "/assets/0"]:
        assert (await ctx.client.get(url, headers=ctx.headers(ctx.users.other))).status_code == 404
    assert (await ctx.client.post(exam_url + "/submit", headers=ctx.headers(ctx.users.teacher))).status_code == 403
    response = await ctx.client.get(question_url, headers=headers)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    question = response.json()
    assert question["question"]["version_id"] == first["version_id"]
    assert question["response"] is None and "answer" not in question and "explanation" not in question
    assert (await ctx.client.get(question["question"]["assets"][0], headers=headers)).status_code == 200

    chosen = {"kind": "option", "option_ids": ["A"]}
    draft = {"expected_revision": 0, "response": chosen}
    saved = await ctx.client.put(question_url, json=draft, headers=headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["answered"] and saved.json()["earned_score"] is None
    assert (await ctx.client.put(question_url, json=draft, headers=headers)).json() == saved.json()
    stale = await ctx.client.put(question_url, json={**draft, "response": None}, headers=headers)
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "written_exam_draft_changed"
    assert (await ctx.client.get(question_url, headers=headers)).json()["response"] == chosen

    hidden = await ctx.client.post(
        f"/api/v1/admin/written-bank/published/{second['id']}/state",
        json={"expected_version": second["catalog_version"], "is_active": False},
        headers=ctx.headers(ctx.users.admin),
    )
    assert hidden.status_code == 200, hidden.text
    resumed = (await ctx.client.post(start, headers=headers)).json()
    assert resumed["id"] == exam["id"] and resumed["deadline"] == exam["deadline"]
    assert len(resumed["items"]) == 2

    clock += timedelta(minutes=10)
    assert (await ctx.client.get(exam_url, headers=headers)).json()["status"] == "expired"
    late = await ctx.client.put(question_url, json={"expected_revision": 1, "response": None}, headers=headers)
    assert late.status_code == 409 and late.json()["detail"]["code"] == "written_exam_closed"
    assert "answer" not in (await ctx.client.get(question_url, headers=headers)).json()
    final = await ctx.client.post(exam_url + "/submit", headers=headers)
    assert final.status_code == 200, final.text
    result = final.json()
    assert result["status"] == "submitted" and result["earned_score"] == 2
    assert result["items"][1]["earned_score"] == 0 and not result["items"][1]["answered"]
    clock += timedelta(minutes=1)
    replay = (await ctx.client.post(exam_url + "/submit", headers=headers)).json()
    assert replay["submitted_at"] == result["submitted_at"] and replay["items"] == result["items"]
    assert (await ctx.client.get(question_url, headers=headers)).json()["answer"]["option_ids"] == ["A"]
    assert (await ctx.client.get(EXAMS, headers=ctx.headers(ctx.users.other))).json()["items"] == []
    assert (await ctx.client.get(EXAMS, headers=headers)).json()["items"][0]["id"] == exam["id"]
    assert await ctx.session.scalar(select(func.count()).select_from(WrittenAttempt)) == 0


async def test_review_links_only_student_retries_to_submitted_missed_versions(api_context):
    ctx = api_context
    records = make_records()
    records["papers.jsonl"][0].update(duration_minutes=10, total_score=4)
    for question in records["questions.jsonl"]:
        question["score"] = 2
    root = await staged(ctx, records)
    first, second = await publish(ctx, root, "q1"), await publish(ctx, root, "q2")
    start = f"{EXAMS}/start/{root.split('/')[-1]}/paper1"
    headers = ctx.headers()
    started = (await ctx.client.post(start, headers=headers)).json()
    exam_id = started["id"]
    url = f"{EXAMS}/{exam_id}"
    review = url + "/review"
    first_question = f"{review}/{first['id']}"
    body = {**attempt(first, single("B")), "exam_id": exam_id}

    async def submit(body, headers=headers):
        return await ctx.client.post("/api/v1/written/attempts", json=body, headers=headers)

    assert (await ctx.client.get(review, headers=headers)).status_code == 409
    assert (await submit(body)).status_code == 409
    for ident, value in ((first["id"], "B"), (second["id"], "A")):
        saved = await ctx.client.put(f"{url}/questions/{ident}", headers=headers, json={
            "expected_revision": 0, "response": single(value),
        })
        assert saved.status_code == 200, saved.text
    submitted = (await ctx.client.post(url + "/submit", headers=headers)).json()
    assert submitted["earned_score"] == 2
    assert (await ctx.client.get(review, headers=ctx.headers(ctx.users.other))).status_code == 404
    assert (await ctx.client.get(first_question, headers=ctx.headers(ctx.users.other))).status_code == 404
    assert (await submit(body, ctx.headers(ctx.users.other))).status_code == 404
    assert (await submit({**body, "assignment_id": 1})).status_code == 422
    assert (await submit({**attempt(second, single("A")), "exam_id": exam_id})).status_code == 404
    question = await ctx.client.get(first_question, headers=headers)
    assert question.status_code == 200 and "answer" not in question.json()
    assert question.json()["version_id"] == first["version_id"]
    initial = (await ctx.client.get(review, headers=headers)).json()["items"]
    assert [row["question_id"] for row in initial] == [first["id"]]
    assert initial[0]["answered"] and initial[0]["available"] and initial[0]["latest_attempt"] is None

    # 普通练对同一题不会被记到这张模拟答卷。
    assert (await submit(attempt(first, single("A")))).json()["correct"] is True
    assert (await ctx.client.get(review, headers=headers)).json()["items"] == initial
    wrong = (await submit(body)).json()
    assert wrong["exam_id"] == exam_id and wrong["correct"] is False
    assert (await submit(body)).json()["id"] == wrong["id"]
    assert (await submit({k: v for k, v in body.items() if k != "exam_id"})).status_code == 409
    correct = (await submit({**body, "request_id": str(uuid4()), "response": single("A")})).json()
    assert correct["correct"] is True and correct["id"] != wrong["id"]
    receipt = await ctx.client.get(f"/api/v1/written/attempts/{correct['id']}", headers=headers)
    assert receipt.json()["exam_id"] == exam_id and receipt.json()["assignment_id"] is None
    row = (await ctx.client.get(review, headers=headers)).json()["items"][0]
    assert row["latest_attempt"] == {"id": correct["id"], "correct": True}
    unchanged = (await ctx.client.get(url, headers=headers)).json()
    assert unchanged["earned_score"] == 2 and unchanged["items"] == submitted["items"]

    await publish(ctx, root, "q1", explanation="已更新解析，旧答卷和旧复练记录不变。")
    updated = (await ctx.client.get(review, headers=headers)).json()["items"][0]
    assert not updated["available"] and updated["latest_attempt"] == row["latest_attempt"]
    assert (await ctx.client.get(first_question, headers=headers)).status_code == 409
    assert (await submit({**body, "request_id": str(uuid4())})).status_code == 409
    assert (await submit(body)).json()["id"] == wrong["id"]
