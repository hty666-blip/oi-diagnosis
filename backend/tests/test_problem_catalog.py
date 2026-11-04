"""独立 MySQL：导入原子性、权限、不可变测试快照及训练版本。"""
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.capabilities.sandbox import CommandResult, CompileResult, SandboxError
from app.domain.models import (
    AdminAudit,
    Problem,
    ProblemImportDraft,
    ProblemVersion,
    Submission,
    User,
)
from app.services.problem_versions import version_digest
from app.services.submission_service import SubmissionService


def problem_input(pid="CAT-SUM", **changes):
    return {"pid": pid, "title": "版本一题面", "statement_md": "读取输入，输出一。",
            "samples": [{"input": "1\n", "output": "1\n"}], "package_level": "C",
            "source": "self-authored", "license_note": "合成验收题，允许本地测试。", **changes}


async def preview(ctx, *records, actor=None):
    response = await ctx.client.post("/api/v1/admin/problems/import/preview", headers=ctx.headers(actor or ctx.users.admin),
                                     json={"problems": list(records), "rights_confirmed": True})
    assert response.status_code == 200, response.text
    return response.json()


async def confirm(ctx, value, actor=None):
    return await ctx.client.post("/api/v1/admin/problems/import/confirm", headers=ctx.headers(actor or ctx.users.admin),
                                 json={"preview_id": value["preview_id"]})


async def install(ctx, record=None, active=True):
    value = await preview(ctx, record or problem_input())
    response = await confirm(ctx, value)
    assert response.status_code == 200, response.text
    row = await ctx.session.get(Problem, response.json()["items"][0]["id"])
    if active:
        response = await ctx.client.post(f"/api/v1/admin/problems/{row.id}/state", headers=ctx.headers(ctx.users.admin),
                                         json={"expected_version": row.catalog_version, "is_active": True})
        assert response.status_code == 200, response.text
    return row


class OneRunner:
    async def compile(self, code):
        return CompileResult(ok=True, stderr="", artifact_id="synthetic")

    async def run(self, artifact_id, **kwargs):
        return CommandResult(status="Accepted", exit_code=0, time_ns=1, memory_bytes=1, stdout="1\n")


async def test_preview_is_read_only_and_confirm_is_idempotent(api_context):
    ctx = api_context
    value = await preview(ctx, problem_input())
    assert await ctx.session.scalar(select(Problem.id).where(Problem.pid == "CAT-SUM")) is None
    response = await confirm(ctx, value)
    assert response.status_code == 200 and not response.json()["items"][0]["is_active"]
    repeated = await confirm(ctx, value)
    assert repeated.status_code == 200 and repeated.json()["replayed"]
    assert repeated.json()["items"] == response.json()["items"]
    assert await ctx.session.scalar(select(func.count()).select_from(ProblemVersion)) == 1
    assert await ctx.session.scalar(select(func.count()).select_from(AdminAudit)) == 1


async def test_same_content_does_not_append_or_retire_active_version(api_context):
    ctx = api_context
    row = await install(ctx)
    value = await preview(ctx, problem_input())
    assert value["rows"][0]["action"] == "unchanged"
    response = await confirm(ctx, value)
    assert response.json()["items"][0]["changed"] is False
    assert row.is_active and row.revision == 1
    assert await ctx.session.scalar(select(func.count()).select_from(ProblemVersion)) == 1


@pytest.mark.parametrize("changes", [
    {"source": " "}, {"license_note": ""}, {"license_note": "请填写实际许可说明"}, {"source_url": "file:///private.env"},
    {"source_url": "https://user:private@example.org"}, {"package_level": "A"},
    {"package_level": "B", "reviewer": "审核人", "cases": [{"input": "1", "output": "1"}], "time_limit_ms": True},
    {"cases": [{"input": "1", "output": "1"}]}, {"samples": [{"input": "1"}]},
    {"package_level": "B", "reviewer": "审核人", "cases": [{"name": "same", "input": "1", "output": "1"}] * 2},
])
async def test_invalid_packages_are_rejected_before_any_draft(api_context, changes):
    ctx = api_context
    response = await ctx.client.post("/api/v1/admin/problems/import/preview", headers=ctx.headers(ctx.users.admin),
                                     json={"problems": [problem_input(**changes)], "rights_confirmed": True})
    assert response.status_code == 422, response.text
    assert "private.env" not in response.text and "user:private" not in response.text
    assert await ctx.session.scalar(select(func.count()).select_from(ProblemImportDraft)) == 0


async def test_import_conflict_rejects_whole_batch(api_context):
    ctx = api_context
    existing = await install(ctx)
    value = await preview(ctx, problem_input("CAT-NEW"), problem_input(title="新题面"))
    response = await ctx.client.post(f"/api/v1/admin/problems/{existing.id}/state", headers=ctx.headers(ctx.users.admin),
                                     json={"expected_version": existing.catalog_version, "is_active": False})
    assert response.status_code == 200
    response = await confirm(ctx, value)
    assert response.status_code == 409
    assert await ctx.session.scalar(select(Problem.id).where(Problem.pid == "CAT-NEW")) is None
    assert existing.revision == 1


async def test_preview_owner_expiry_and_role_are_enforced(api_context):
    ctx = api_context
    value = await preview(ctx, problem_input())
    other = User(username="another_catalog_admin", role="admin")
    ctx.session.add(other)
    await ctx.session.flush()
    assert (await confirm(ctx, value, other)).status_code == 404
    assert (await confirm(ctx, value, ctx.users.teacher)).status_code == 403
    draft = await ctx.session.get(ProblemImportDraft, value["preview_id"])
    draft.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await ctx.session.flush()
    assert (await confirm(ctx, value)).status_code == 409
    for actor in (ctx.users.student, ctx.users.teacher):
        assert (await ctx.client.get("/api/v1/admin/problems", headers=ctx.headers(actor))).status_code == 403


async def test_queued_submission_and_parent_continue_original_snapshot(api_context, monkeypatch):
    ctx = api_context
    row = await install(ctx)
    service = SubmissionService(ctx.session, sandbox=OneRunner())
    original = await service.create(row.pid, "print(1)", ctx.users.student.id, language="python3", background=True)
    old_version = original.problem_version_id
    await install(ctx, problem_input(title="版本二题面", statement_md="读取输入，输出二。", samples=[{"input": "1\n", "output": "2\n"}]))
    assert row.revision == 2
    # 即使磁盘题包不可读，后台仍使用已经保存的测试快照。
    def forbidden(*args, **kwargs):
        raise AssertionError("固定版本不应再读取磁盘题包")
    monkeypatch.setattr("app.capabilities.test_suite.load_package", forbidden)
    result = await service.run(original)
    assert result.verdict == "SAMPLE_PASS"
    newer = await service.create(row.pid, "print(1)", ctx.users.student.id, language="python3")
    assert newer.problem_version_id != old_version and newer.id != original.id
    assert (await service.run(newer)).verdict == "WA"
    correction = await service.create(row.pid, "print(1) # 修改说明", ctx.users.student.id, original.id, language="python3")
    assert correction.problem_version_id == old_version
    assert (await service.run(correction)).verdict == "SAMPLE_PASS"
    response = await ctx.client.get(f"/api/v1/submissions/{original.id}", headers=ctx.headers())
    assert response.json()["problem_title"] == "版本一题面"
    response = await ctx.client.get(f"/api/v1/problems/{row.pid}?submission_id={original.id}", headers=ctx.headers())
    assert response.json()["revision"] == 1


async def test_training_stays_on_pinned_version_and_retired_new_use_is_rejected(api_context):
    ctx = api_context
    row = await install(ctx)
    response = await ctx.client.post("/api/v1/trainings", headers=ctx.headers(ctx.users.teacher), json={
        "title": "固定版本训练", "class_name": "A", "due_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "problems": [{"pid": row.pid}]})
    assert response.status_code == 200, response.text
    tid = response.json()["id"]
    response = await ctx.client.post(f"/api/v1/trainings/{tid}/publish", headers=ctx.headers(ctx.users.teacher))
    assert response.status_code == 200
    await install(ctx, problem_input(title="更新后的题面"), active=False)
    assert not row.is_active
    listed = (await ctx.client.get("/api/v1/problems", headers=ctx.headers())).json()
    assert row.pid not in {p["pid"] for p in listed}
    assert (await ctx.client.get(f"/api/v1/problems/{row.pid}", headers=ctx.headers())).status_code == 404
    detail = await ctx.client.get(f"/api/v1/problems/{row.pid}?training_id={tid}", headers=ctx.headers())
    assert detail.status_code == 200 and detail.json()["title"] == "版本一题面"
    assert (await ctx.client.get(f"/api/v1/problems/{row.pid}?training_id={tid}", headers=ctx.headers(ctx.users.other))).status_code == 404
    service = SubmissionService(ctx.session, sandbox=OneRunner())
    sub = await service.create(row.pid, "print(1)", ctx.users.student.id, language="python3", training_id=tid)
    assert sub.problem_version_id == detail.json()["version_id"]
    assert (await service.run(sub)).verdict == "SAMPLE_PASS"
    response = await ctx.client.post("/api/v1/submissions", headers=ctx.headers(), json={"problem_pid": row.pid, "code": "print(1)", "language": "python3"})
    assert response.status_code == 409
    task = (await ctx.client.get(f"/api/v1/trainings/{tid}", headers=ctx.headers())).json()
    assert task["problems"][0]["title"] == "版本一题面"


async def test_stale_client_version_and_corrupt_snapshot_fail_closed(api_context):
    ctx = api_context
    row = await install(ctx)
    version = await ctx.session.scalar(select(ProblemVersion).where(ProblemVersion.problem_id == row.id))
    await install(ctx, problem_input(title="版本二"))
    response = await ctx.client.post("/api/v1/submissions", headers=ctx.headers(), json={
        "problem_pid": row.pid, "code": "print(1)", "problem_version_id": version.id})
    assert response.status_code == 409
    assert await ctx.session.scalar(select(func.count()).select_from(Submission)) == 0
    current = await ctx.session.scalar(select(ProblemVersion).where(ProblemVersion.problem_id == row.id, ProblemVersion.revision == 2))
    current.digest = "0" * 64
    await ctx.session.flush()
    response = await ctx.client.post("/api/v1/submissions", headers=ctx.headers(), json={"problem_pid": row.pid, "code": "print(1)"})
    assert response.status_code == 503
    response = await ctx.client.post(f"/api/v1/admin/problems/{row.id}/state", headers=ctx.headers(ctx.users.admin),
                                     json={"expected_version": row.catalog_version, "is_active": False})
    assert response.status_code == 200 and not response.json()["is_active"]


async def test_formal_tests_are_pinned_but_not_exposed_by_student_detail(api_context, monkeypatch):
    ctx = api_context
    record = problem_input(package_level="A", reviewer="合成审核人", cases=[
        {"name": f"hidden{i}", "input": str(i), "output": "1\n"} for i in range(3)])
    row = await install(ctx, record)
    response = await ctx.client.get(f"/api/v1/problems/{row.pid}", headers=ctx.headers())
    assert response.status_code == 200 and "hidden0" not in response.text
    assert len(response.json()["samples"]) == 1
    service = SubmissionService(ctx.session, sandbox=OneRunner())
    sub = await service.create(row.pid, "print(1)", ctx.users.student.id, language="python3")
    def forbidden(*args, **kwargs):
        raise AssertionError("正式测试应读取数据库快照")
    monkeypatch.setattr("app.capabilities.test_suite.load_package", forbidden)
    result = await service.run(sub)
    assert result.verdict == "AC" and result.total == 3
    versions = (await ctx.client.get(f"/api/v1/admin/problems/{row.id}/versions", headers=ctx.headers(ctx.users.admin))).json()
    assert versions["items"][0]["cases"] == 3


async def test_rights_and_duplicate_pids_are_rejected(api_context):
    ctx = api_context
    for body in ({"problems": [problem_input()], "rights_confirmed": False},
                 {"problems": [problem_input(), problem_input("cat-sum")], "rights_confirmed": True}):
        response = await ctx.client.post("/api/v1/admin/problems/import/preview", headers=ctx.headers(ctx.users.admin), json=body)
        assert response.status_code == 422
    response = await ctx.client.post("/api/v1/admin/problems/import/preview", headers={**ctx.headers(ctx.users.admin), "Content-Type": "application/json"},
                                     content=json.dumps({"problems": [problem_input(statement_md="bad\ud800text")], "rights_confirmed": True}))
    assert response.status_code == 422


async def test_publishing_old_draft_requires_explicit_reselection(api_context):
    ctx = api_context
    row = await install(ctx)
    response = await ctx.client.post("/api/v1/trainings", headers=ctx.headers(ctx.users.teacher), json={
        "title": "更新前草稿", "class_name": "A", "due_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "problems": [{"pid": row.pid}]})
    assert response.status_code == 200
    tid = response.json()["id"]
    await install(ctx, problem_input(title="版本二"))
    response = await ctx.client.post(f"/api/v1/trainings/{tid}/publish", headers=ctx.headers(ctx.users.teacher))
    assert response.status_code == 409
    detail = await ctx.client.get(f"/api/v1/trainings/{tid}", headers=ctx.headers(ctx.users.teacher))
    assert detail.json()["status"] == "draft" and detail.json()["problems"][0]["title"] == "版本一题面"


async def test_missing_old_suite_stops_worker_before_compile(api_context):
    ctx = api_context
    row = await install(ctx)
    class SpyRunner(OneRunner):
        called = False
        async def compile(self, code):
            self.called = True
            return await super().compile(code)
    runner = SpyRunner()
    service = SubmissionService(ctx.session, sandbox=runner)
    sub = await service.create(row.pid, "print(1)", ctx.users.student.id, language="python3", background=True)
    old = await ctx.session.get(ProblemVersion, sub.problem_version_id)
    await install(ctx, problem_input(title="版本二"))
    old.suite = None
    old.digest = version_digest(old.data, None, old.reviewer)
    await ctx.session.commit()
    with pytest.raises(SandboxError):
        await service.run(sub)
    assert not runner.called and sub.stage == "SANDBOX_ERROR"


async def test_other_version_ac_cannot_resolve_original_tutor_session(api_context):
    ctx = api_context
    record = problem_input(package_level="A", reviewer="合成验收", cases=[{"name": f"test{i}", "input": str(i), "output": "2"} for i in range(3)])
    row = await install(ctx, record)
    service = SubmissionService(ctx.session, sandbox=OneRunner())
    original = await service.create(row.pid, "print(1)", ctx.users.student.id, language="python3")
    assert (await service.run(original)).verdict == "WA"
    response = await ctx.client.post("/api/v1/sessions", headers=ctx.headers(), json={"submission_id": original.id})
    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]
    record["title"] = "新版题目"
    record["cases"] = [{**c, "output": "1"} for c in record["cases"]]
    await install(ctx, record)
    candidate = await service.create(row.pid, "print(1) # 新版本", ctx.users.student.id, language="python3")
    assert (await service.run(candidate)).verdict == "AC"
    response = await ctx.client.post(f"/api/v1/sessions/{session_id}/verify", headers=ctx.headers(), json={"submission_id": candidate.id})
    assert response.status_code == 409 and "不同题目版本" in response.text
