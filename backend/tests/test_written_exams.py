"""整卷模拟的时间、固定题面、草稿冲突和不可重复交卷。"""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select
from test_written_assignments import groups
from test_written_attempts import attempt, single
from test_written_bank_import import make_records
from test_written_knowledge import ARTICLE, POINT, create_entry, edit
from test_written_progress import publish
from test_written_review import staged

from app.api.v1 import written_exams
from app.domain.models import WrittenAttempt, WrittenQuestionVersion
from app.services import written_exam_review

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


async def test_coach_mock_results_follow_current_class_and_linked_retries(api_context, monkeypatch):
    ctx = api_context
    group, other_group = await groups(ctx)
    records = make_records()
    records["papers.jsonl"][0].update(duration_minutes=10, total_score=4)
    for question in records["questions.jsonl"]:
        question["score"] = 2
    root = await staged(ctx, records)
    first = await publish(ctx, root, "q1")
    second = await publish(ctx, root, "q2")
    start = f"{EXAMS}/start/{root.split('/')[-1]}/paper1"
    urls = {group.id: f"{EXAMS}/classes/{group.id}/results", other_group.id: f"{EXAMS}/classes/{other_group.id}/results"}
    coach = ctx.headers(ctx.users.teacher)
    headers = ctx.headers()

    async def begin(user):
        result = await ctx.client.post(start, headers=ctx.headers(user))
        assert result.status_code == 200, result.text
        return result.json()["id"]

    exam_id = await begin(ctx.users.student)
    peer_exam_id = await begin(ctx.users.peer)
    other_exam_id = await begin(ctx.users.other)
    assert (await ctx.client.get(urls[group.id], headers=headers)).status_code == 403
    assert (await ctx.client.get(urls[other_group.id], headers=coach)).status_code == 404
    assert (await ctx.client.get(urls[group.id], headers=coach)).json()["items"] == []
    assert (await ctx.client.post(f"{EXAMS}/{other_exam_id}/submit", headers=ctx.headers(ctx.users.other))).status_code == 200

    saved = await ctx.client.put(f"{EXAMS}/{exam_id}/questions/{first['id']}", headers=headers, json={
        "expected_revision": 0, "response": single("A"),
    })
    assert saved.status_code == 200, saved.text
    submitted = await ctx.client.post(f"{EXAMS}/{exam_id}/submit", headers=headers)
    assert submitted.status_code == 200, submitted.text
    expected = {
        "id": exam_id, "student_name": ctx.users.student.display_name, "title": submitted.json()["title"],
        "submitted_at": submitted.json()["submitted_at"], "earned_score": 2, "max_score": 4,
        "missed_count": 1, "corrected_count": 0,
    }
    response = await ctx.client.get(urls[group.id], headers=coach)
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"items": [expected], "next_before_id": None}

    async def retry(choice, *, linked):
        body = attempt(second, single(choice))
        if linked:
            body["exam_id"] = exam_id
        result = await ctx.client.post("/api/v1/written/attempts", json=body, headers=headers)
        assert result.status_code == 200, result.text
        return result.json()["correct"]

    assert await retry("A", linked=False) is True
    assert (await ctx.client.get(urls[group.id], headers=coach)).json()["items"] == [expected]
    assert await retry("A", linked=True) is True
    assert (await ctx.client.get(urls[group.id], headers=coach)).json()["items"] == [
        {**expected, "corrected_count": 1}
    ]
    assert await retry("B", linked=True) is False
    assert (await ctx.client.get(urls[group.id], headers=coach)).json()["items"] == [expected]

    assert (await ctx.client.post(f"{EXAMS}/{peer_exam_id}/submit", headers=ctx.headers(ctx.users.peer))).status_code == 200
    monkeypatch.setattr(written_exam_review, "CLASS_RESULT_LIMIT", 1)
    page = (await ctx.client.get(urls[group.id], headers=coach)).json()
    assert [item["id"] for item in page["items"]] == [peer_exam_id]
    assert page["next_before_id"] == peer_exam_id
    page = (await ctx.client.get(urls[group.id], headers=coach, params={"before_id": peer_exam_id})).json()
    assert page == {"items": [expected], "next_before_id": None}

    ctx.users.student.class_id = other_group.id
    ctx.users.peer.enabled = False
    await ctx.session.flush()
    assert (await ctx.client.get(urls[group.id], headers=coach)).json()["items"] == []
    admin = ctx.headers(ctx.users.admin)
    result = await ctx.client.get(urls[other_group.id], headers=admin, params={"before_id": other_exam_id})
    assert result.json()["items"] == [expected]
    other_group.archived = True
    await ctx.session.flush()
    assert (await ctx.client.get(urls[other_group.id], headers=admin)).status_code == 404


async def test_exam_study_groups_frozen_topics_and_only_recommends_unseen_published_questions(api_context):
    ctx = api_context
    records = make_records()
    paper = records["papers.jsonl"][0]
    paper.update(duration_minutes=10, total_score=4)
    records["papers.jsonl"].append({**paper, "id": "extra", "title": "补充练习"})
    records["knowledge_points.json"]["nodes"].append({"id": "TIME", "name": "时间复杂度", "is_leaf": True})
    for question in records["questions.jsonl"]:
        question["score"] = 2
    first = records["questions.jsonl"][0]
    for ident, changes in [
        ("duplicate", {"relation_to_canonical": "exact_duplicate"}),
        ("advanced", {"stem": "提高组题面", "canonical_question_id": "advanced", "level": "S"}),
        ("new", {"stem": "尚未作答的新题", "canonical_question_id": "new"}),
        ("unpublished", {"stem": "待发布题面", "canonical_question_id": "unpublished"}),
    ]:
        records["questions.jsonl"].append({**deepcopy(first), "id": ident, "paper_id": "extra", **changes})
    root = await staged(ctx, records)
    batch = root.split("/")[-1]
    original = await publish(ctx, root, "q1")
    for ident in ("q2", "duplicate", "advanced"):
        await publish(ctx, root, ident)
    fresh = await publish(ctx, root, "new")
    old = await ctx.session.get(WrittenQuestionVersion, original["version_id"])
    old.snapshot_metadata = {key: value for key, value in old.snapshot_metadata.items() if key != "learning_topic"}
    await ctx.session.flush()
    draft = await create_entry(ctx, batch)
    headers = ctx.headers()
    started = await ctx.client.post(f"{EXAMS}/start/{batch}/paper1", headers=headers)
    assert started.status_code == 200, started.text
    url = f"{EXAMS}/{started.json()['id']}"
    assert (await ctx.client.get(url + "/study", headers=headers)).status_code == 409
    assert (await ctx.client.get(url + "/study", headers=ctx.headers(ctx.users.peer))).status_code == 404
    assert (await ctx.client.get(url + "/study", headers=ctx.headers(ctx.users.teacher))).status_code == 403
    assert (await ctx.client.post(url + "/submit", headers=headers)).status_code == 200

    async def topic():
        result = await ctx.client.get(url + "/study", headers=headers)
        assert result.status_code == 200, result.text
        assert result.headers["cache-control"] == "no-store"
        [item] = result.json()["items"]
        return item

    initial = await topic()
    assert initial["numbers"] == ["1", "2"] and initial["level"] == "J"
    assert initial["point"]["id"] == POINT and initial["article"] is None
    assert initial["next_question"] == {"id": fresh["id"], "version_id": fresh["version_id"]}
    published = await edit(ctx, draft, "publish", ARTICLE)
    assert published.status_code == 200, published.text
    await publish(ctx, root, "q1", level="S", primary_knowledge_point_id="TIME")
    other = await ctx.client.post("/api/v1/written/attempts", json=attempt(fresh, single("A")),
                                  headers=ctx.headers(ctx.users.peer))
    assert other.status_code == 200, other.text
    assert await topic() == {**initial, "article": {"id": draft["id"], "title": ARTICLE["title"]}}

    solved = await ctx.client.post("/api/v1/written/attempts", json=attempt(fresh, single("A")), headers=headers)
    assert solved.status_code == 200 and solved.json()["correct"] is True and solved.json().get("exam_id") is None
    assert (await topic())["next_question"] is None
    withdrawn = await edit(ctx, published.json(), "unpublish")
    assert withdrawn.status_code == 200, withdrawn.text
    assert (await topic())["article"] is None
    result = (await ctx.client.get(url, headers=headers)).json()
    assert result["earned_score"] == 0 and result["max_score"] == 4
    original_review = (await ctx.client.get(url + "/review", headers=headers)).json()["items"]
    assert all(item["latest_attempt"] is None for item in original_review)
