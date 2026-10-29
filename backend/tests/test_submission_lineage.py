import asyncio

import pytest

from app.capabilities.sandbox import CommandResult, CompileResult
from app.domain.models import Problem, Submission
from app.services.submission_service import SubmissionService


class Runner:
    calls = 0

    async def compile(self, code):
        self.calls += 1
        return CompileResult(ok=True, stderr="", artifact_id="in-memory")

    async def run(self, artifact_id, **kwargs):
        return CommandResult(status="Accepted", exit_code=0, time_ns=1,
                             memory_bytes=1, stdout="1")


async def send(ctx, code, parent=None, user=None):
    return await ctx.client.post("/api/v1/submissions", headers=ctx.headers(user), json={
        "problem_pid": ctx.problem.pid, "code": code, "parent_submission_id": parent,
    })


async def test_lineage_and_cache_are_scoped_to_the_parent(api_context, monkeypatch):
    ctx = api_context
    monkeypatch.setattr("app.services.submission_service.build_runner", Runner)
    ctx.problem.samples = [{"input": "1", "output": "1"}]
    await ctx.session.commit()
    first = (await send(ctx, "int main(){return 1;}")).json()
    second = (await send(ctx, "int main(){return 2;}")).json()
    child = (await send(ctx, "int main(){return 3;}", first["id"])).json()
    same = (await send(ctx, "int main(){return 3;}", first["id"])).json()
    other_branch = (await send(ctx, "int main(){return 3;}", second["id"])).json()
    assert child["parent_submission_id"] == first["id"]
    assert same["id"] == child["id"]
    assert other_branch["id"] != child["id"]
    lineage = await ctx.client.get(f"/api/v1/submissions/{child['id']}/lineage", headers=ctx.headers())
    assert lineage.status_code == 200
    assert [row["id"] for row in lineage.json()] == [first["id"], child["id"]]
    assert lineage.json()[-1]["code"] == "int main(){return 3;}"
    denied = await ctx.client.get(f"/api/v1/submissions/{child['id']}/lineage",
                                   headers=ctx.headers(ctx.users.peer))
    assert denied.status_code == 404


async def test_parent_cannot_be_another_students_submission_or_another_problem(api_context, monkeypatch):
    ctx = api_context
    monkeypatch.setattr("app.services.submission_service.build_runner", Runner)
    other = await ctx.submission(user=ctx.users.peer)
    assert (await send(ctx, "int main(){}", other.id)).status_code == 404
    another_problem = Problem(pid="TEST-LINEAGE-OTHER", title="另一题", samples=[])
    ctx.session.add(another_problem)
    await ctx.session.flush()
    different = await ctx.submission(target_problem=another_problem)
    assert (await send(ctx, "int main(){}", different.id)).status_code == 404


async def test_inflight_duplicate_does_not_compile_twice(api_context, monkeypatch):
    ctx = api_context
    runner = Runner()
    svc = SubmissionService(ctx.session, sandbox=runner)
    original = await svc.create(ctx.problem.pid, "int main(){}", ctx.users.student.id)
    monkeypatch.setattr("app.services.submission_service.build_runner", lambda: runner)
    duplicate = await send(ctx, "int main(){}")
    assert duplicate.status_code == 409
    assert duplicate.headers["Location"].endswith(f"/{original.id}")
    assert runner.calls == 0


async def test_cancelled_execution_is_not_permanently_running(api_context):
    ctx = api_context

    class CancelledRunner(Runner):
        async def compile(self, code):
            raise asyncio.CancelledError

    svc = SubmissionService(ctx.session, sandbox=CancelledRunner())
    sub = await svc.create(ctx.problem.pid, "int main(){}", ctx.users.student.id)
    submission_id = sub.id
    with pytest.raises(asyncio.CancelledError):
        await svc.run(sub)
    saved = await ctx.session.get(Submission, submission_id, populate_existing=True)
    assert saved.stage == "INTERRUPTED"
    assert saved.verdict is None
    replacement = await SubmissionService(ctx.session, sandbox=Runner()).create(
        ctx.problem.pid, "int main(){}", ctx.users.student.id
    )
    assert replacement.id != submission_id
