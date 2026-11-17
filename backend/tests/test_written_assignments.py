"""从教练发布到学生作答及班级学情，使用真实 MySQL 接口。"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from test_written_attempts import attempt, single, submit
from test_written_progress import publish
from test_written_review import staged

from app.domain.models import ClassTeacher, SchoolClass, WrittenAssignment

ASSIGNMENTS = "/api/v1/written/assignments"


async def groups(ctx):
    first, other = SchoolClass(name="初赛 J 班"), SchoolClass(name="初赛 S 班")
    ctx.session.add_all([first, other])
    await ctx.session.flush()
    ctx.session.add_all([
        ClassTeacher(class_id=first.id, teacher_id=ctx.users.teacher.id),
        ClassTeacher(class_id=other.id, teacher_id=ctx.users.other_teacher.id),
    ])
    for student, group in ((ctx.users.student, first), (ctx.users.peer, first), (ctx.users.other, other)):
        student.class_id = group.id
        student.class_name = group.name
    await ctx.session.flush()
    return first, other


def task_body(group, *versions):
    return {
        "class_id": group.id,
        "title": "程序空间专项",
        "request_id": str(uuid4()),
        "due_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "questions": [{"id": v["id"], "version_id": v["version_id"]} for v in versions],
    }


async def send(ctx, body, user=None):
    return await ctx.client.post(ASSIGNMENTS, json=body, headers=ctx.headers(user or ctx.users.teacher))


async def task(ctx, path, user=None):
    result = await ctx.client.get(path, headers=ctx.headers(user))
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "no-store"
    return result.json()


async def test_fixed_assignment_version_and_separate_progress(api_context):
    ctx = api_context
    group, other = await groups(ctx)
    source = await staged(ctx)
    first = await publish(ctx, source, "q1")
    second = await publish(ctx, source, "q2")
    assert await task(ctx, f"{ASSIGNMENTS}/classes", ctx.users.teacher) == [
        {"id": group.id, "name": group.name}
    ]
    assert (await send(ctx, task_body(other, first))).status_code == 404
    assert (await send(ctx, task_body(group, first), ctx.users.student)).status_code == 403
    body = task_body(group, first)
    response = await send(ctx, body)
    assert response.status_code == 200, response.text
    assignment_id = response.json()["id"]
    path = f"{ASSIGNMENTS}/{assignment_id}"
    repeated = await send(ctx, body)
    assert repeated.json()["id"] == assignment_id and repeated.json()["replayed"]
    assert (await send(ctx, {**body, "title": "不同任务"})).status_code == 409
    assert (await ctx.client.get(path, headers=ctx.headers(ctx.users.other_teacher))).status_code == 404
    assert (await ctx.client.get(path, headers=ctx.headers(ctx.users.other))).status_code == 404
    assert [item["id"] for item in await task(ctx, ASSIGNMENTS)] == [assignment_id]
    assert not await task(ctx, ASSIGNMENTS, ctx.users.other)

    assert (await submit(ctx, attempt(first, single("A")))).json()["correct"]
    coach = await task(ctx, path, ctx.users.teacher)
    assert coach["summary"] == {"students": 2, "submitted_all": 0, "correct_all": 0}
    assert [row["student_id"] for row in coach["rows"]] == [ctx.users.student.id, ctx.users.peer.id]

    changed = await publish(
        ctx, source, "q1", answer=single("B"),
        options=[{"id": "A", "text": "3"}, {"id": "B", "text": "2"}],
        explanation="一加一等于二，新版把正确选项放在 B。",
    )
    q_path = f"{path}/questions/{first['id']}"
    frozen = await task(ctx, q_path)
    assert frozen["version_id"] == first["version_id"] != changed["version_id"]
    assert "answer" not in frozen and "explanation" not in frozen
    assert (await send(ctx, task_body(group, first))).status_code == 409
    image = frozen["assets"][0]
    assert (await ctx.client.get(image, headers=ctx.headers())).status_code == 200
    assert (await ctx.client.get(image, headers=ctx.headers(ctx.users.other))).status_code == 404
    invalid = {**attempt(changed, single("B")), "assignment_id": assignment_id}
    assert (await submit(ctx, invalid)).status_code == 409
    invalid = {**attempt(second, single("A")), "assignment_id": assignment_id}
    assert (await submit(ctx, invalid)).status_code == 409
    bound = {**attempt(first, single("A")), "assignment_id": assignment_id}
    stored = await submit(ctx, bound)
    assert stored.status_code == 200 and stored.json()["correct"]
    assert stored.json()["assignment_id"] == assignment_id
    assert (await task(ctx, f"/api/v1/written/attempts/{stored.json()['id']}"))["assignment_id"] == assignment_id
    assert (await submit(ctx, bound, ctx.users.other)).status_code == 404
    assert (await submit(ctx, {k: v for k, v in bound.items() if k != "assignment_id"})).status_code == 409
    student = await task(ctx, path)
    assert [row["student_id"] for row in student["rows"]] == [ctx.users.student.id]
    assert student["rows"][0]["cells"][0]["id"] == stored.json()["id"]
    assert (await task(ctx, path, ctx.users.teacher))["summary"] == {
        "students": 2, "submitted_all": 1, "correct_all": 1,
    }

    hidden = await ctx.client.post(
        f"/api/v1/admin/written-bank/published/{changed['id']}/state",
        json={"is_active": False, "expected_version": changed["catalog_version"]},
        headers=ctx.headers(ctx.users.admin),
    )
    assert hidden.status_code == 200, hidden.text
    hidden_question = (await task(ctx, path))["questions"][0]
    assert not hidden_question["is_available"] and hidden_question["stem"] == f"题目 #{first['id']}"
    assert (await ctx.client.get(q_path, headers=ctx.headers())).status_code == 404
    assert (await submit(ctx, {**bound, "request_id": str(uuid4())})).status_code == 409
    assert (await submit(ctx, bound)).json()["replayed"]


async def test_deadline_roster_snapshot_and_class_changes(api_context):
    ctx = api_context
    group, other = await groups(ctx)
    first = await publish(ctx, await staged(ctx), "q1")
    response = await send(ctx, task_body(group, first))
    assert response.status_code == 200, response.text
    assignment_id = response.json()["id"]
    path = f"{ASSIGNMENTS}/{assignment_id}"
    bound = {**attempt(first, single("B")), "assignment_id": assignment_id}
    first_attempt = await submit(ctx, bound)
    assert first_attempt.status_code == 200, first_attempt.text
    assert not first_attempt.json()["correct"]
    second = await submit(ctx, {**attempt(first, single("A")), "assignment_id": assignment_id})
    assert second.status_code == 200 and second.json()["correct"]
    assigned = await ctx.session.get(WrittenAssignment, assignment_id)
    assigned.due_at = datetime.now(UTC) - timedelta(seconds=1)
    ctx.users.other.class_id = group.id
    await ctx.session.flush()
    detail = await task(ctx, path)
    assert detail["is_closed"] and detail["rows"][0]["cells"][0]["id"] == second.json()["id"]
    assert (await submit(ctx, {**bound, "request_id": str(uuid4())})).status_code == 409
    assert (await submit(ctx, bound)).json()["id"] == first_attempt.json()["id"]
    assert (await ctx.client.get(path, headers=ctx.headers(ctx.users.other))).status_code == 404
    ctx.users.student.class_id = other.id
    await ctx.session.flush()
    assert (await ctx.client.get(path, headers=ctx.headers())).status_code == 404
    coach = await task(ctx, path, ctx.users.teacher)
    assert [row["student_id"] for row in coach["rows"]] == [ctx.users.peer.id]
    assert (await submit(ctx, bound)).json()["id"] == first_attempt.json()["id"]
