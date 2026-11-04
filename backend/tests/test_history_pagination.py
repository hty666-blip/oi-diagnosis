from sqlalchemy import event, select

from app.domain.models import Diagnosis


async def test_problem_picker_states_sample_scope_without_promising_complete_verification(api_context):
    ctx = api_context
    ctx.problem.package_level = "A"
    ctx.problem.has_testdata = False
    await ctx.session.flush()
    bank = await ctx.client.get("/api/v1/problems", headers=ctx.headers())
    assert bank.json()[0]["verification_scope"] == "samples"
    detail = await ctx.client.get(f"/api/v1/problems/{ctx.problem.pid}", headers=ctx.headers())
    assert detail.json()["verification_scope"] == "samples"


async def test_submission_cursor_reaches_older_records_without_other_students(api_context):
    ctx = api_context
    old = await ctx.submission(code_hash="old")
    new = await ctx.submission(code_hash="new")
    await ctx.submission(ctx.users.peer, code_hash="private-peer")
    first = await ctx.client.get("/api/v1/me/submissions?limit=1", headers=ctx.headers())
    assert [row["id"] for row in first.json()] == [new.id]
    older = await ctx.client.get(f"/api/v1/me/submissions?limit=1&before_id={new.id}", headers=ctx.headers())
    assert [row["id"] for row in older.json()] == [old.id]
    end = await ctx.client.get(f"/api/v1/me/submissions?before_id={old.id}", headers=ctx.headers())
    assert end.json() == []
    assert (await ctx.client.get("/api/v1/me/submissions?before_id=0", headers=ctx.headers())).status_code == 422


async def test_review_cursor_and_lightweight_overview_keep_class_scope_and_verdicts(api_context):
    ctx = api_context
    old = await ctx.submission(code_hash="old", verdict="AC",
                               tests_passed={"scope":"package","passed":True,"total":1})
    new = await ctx.submission(code_hash="new")
    foreign = await ctx.submission(ctx.users.other, code_hash="foreign")
    diagnoses = (await ctx.session.scalars(select(Diagnosis).order_by(Diagnosis.id))).all()
    for row in diagnoses:
        row.status = "needs_human"
        row.model_meta = {"route": "secret" if row.submission_id == foreign.id else "human"}
    await ctx.session.flush()
    own = [d for d in diagnoses if d.submission_id in (old.id, new.id)]
    first = await ctx.client.get("/api/v1/coach/queue?limit=1", headers=ctx.headers(ctx.users.teacher))
    assert [row["diagnosis_id"] for row in first.json()] == [own[-1].id]
    older = await ctx.client.get(f"/api/v1/coach/queue?limit=1&before_id={own[-1].id}", headers=ctx.headers(ctx.users.teacher))
    assert [row["diagnosis_id"] for row in older.json()] == [own[0].id]
    statements = []

    def capture(connection, cursor, statement, parameters, context, many):
        statements.append(statement)

    connection = ctx.session.bind.sync_connection
    event.listen(connection, "before_cursor_execute", capture)
    try:
        response = await ctx.client.get("/api/v1/coach/overview", headers=ctx.headers(ctx.users.teacher))
    finally:
        event.remove(connection, "before_cursor_execute", capture)
    assert response.status_code == 200, response.text
    assert response.json()["verdict"] == {"UNVERIFIED":1,"WA":1}
    assert response.json()["route"] == {"human":2}
    assert response.json()["pending_review"] == 2
    assert not any("submission.code," in statement or "diagnosis.narrative" in statement for statement in statements)
