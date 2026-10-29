"""真实 PostgreSQL 接口回归；RUN_DOCKER_TESTS=1 时额外执行真实 C++ 判题。"""

import os

import pytest

from app.capabilities.sandbox import CommandResult, CompileResult, SandboxError
from app.domain.models import Problem


class SampleRunner:
    def __init__(self, fail_at=""):
        self.fail_at = fail_at

    async def compile(self, code):
        if self.fail_at == "compile":
            raise SandboxError("测试：执行器未启动")
        return CompileResult(ok=True, stderr="", artifact_id="memory")

    async def run(self, artifact_id, **kw):
        if self.fail_at == "run":
            raise SandboxError("测试：运行连接中断")
        return CommandResult(status="Accepted", exit_code=0, time_ns=1, memory_bytes=1, stdout="1\n")


def sample_problem(ctx):
    ctx.problem.samples = [{"input": "1\n", "output": "1\n"}]
    ctx.problem.has_testdata = False
    ctx.problem.package_level = "C"
    return ctx.problem


async def submit(ctx, code="int main() { return 0; }", problem=None):
    return await ctx.client.post("/api/v1/submissions", headers=ctx.headers(), json={
        "problem_pid": (problem or ctx.problem).pid, "code": code,
    })


async def test_sample_only_and_missing_tests_are_never_ac(api_context, monkeypatch):
    ctx = api_context
    monkeypatch.setattr("app.services.submission_service.build_runner", SampleRunner)
    sample_problem(ctx)
    await ctx.session.commit()
    result = await submit(ctx)
    assert result.status_code == 200, result.text
    assert result.json()["verdict"] == "SAMPLE_PASS"
    assert result.json()["tests_passed"]["scope"] == "samples"
    diag = await ctx.client.post(f"/api/v1/submissions/{result.json()['id']}/diagnose",
                                 headers=ctx.headers())
    assert diag.status_code == 200
    assert diag.json()["status"] == "inconclusive"

    ctx.problem.samples = []
    await ctx.session.commit()
    no_tests = await submit(ctx)
    assert no_tests.status_code == 200
    assert no_tests.json()["verdict"] == "NO_TESTS"
    assert no_tests.json()["id"] != result.json()["id"]


@pytest.mark.parametrize("fail_at", ["compile", "run"])
async def test_infrastructure_failure_remains_retryable(api_context, monkeypatch, fail_at):
    ctx = api_context
    sample_problem(ctx)
    await ctx.session.commit()
    monkeypatch.setattr("app.services.submission_service.build_runner",
                        lambda: SampleRunner(fail_at))
    failed = await submit(ctx)
    assert failed.status_code == 503
    history = await ctx.client.get("/api/v1/me/submissions", headers=ctx.headers())
    failed_record = history.json()[0]
    assert failed_record["stage"] == "SANDBOX_ERROR"
    assert failed_record["verdict"] is None
    monkeypatch.setattr("app.services.submission_service.build_runner", SampleRunner)
    retried = await submit(ctx)
    assert retried.status_code == 200, retried.text
    assert retried.json()["stage"] == "READY"
    assert retried.json()["verdict"] == "SAMPLE_PASS"
    assert retried.json()["id"] != failed_record["id"]


async def test_cache_is_invalidated_when_test_data_changes(api_context, monkeypatch):
    ctx = api_context
    monkeypatch.setattr("app.services.submission_service.build_runner", SampleRunner)
    sample_problem(ctx)
    await ctx.session.commit()
    first = (await submit(ctx)).json()
    cached = (await submit(ctx)).json()
    assert cached["id"] == first["id"]
    ctx.problem.samples = [{"input": "1\n", "output": "2\n"}]
    await ctx.session.commit()
    changed = (await submit(ctx)).json()
    assert changed["id"] != first["id"]
    assert changed["verdict"] == "WA"
    assert changed["tests_passed"]["suite_digest"] != first["tests_passed"]["suite_digest"]


@pytest.mark.skipif(os.getenv("RUN_DOCKER_TESTS") != "1", reason="需要显式启用 Docker 判题集成")
async def test_real_cpp_fix_is_verified_before_tutor_resolves(api_context):
    ctx = api_context
    problem = Problem(pid="PTV-0001", title="求和", statement_md="自建求和题",
                      package_level="A", has_testdata=True, samples=[])
    ctx.session.add(problem)
    await ctx.session.commit()
    wrong_code = (
        '#include <iostream>\nint main(){int n,x,s=0;std::cin>>n;'
        'while(n--){std::cin>>x;s+=x;}std::cout<<s<<"\\n";}'
    )
    wrong = await submit(ctx, wrong_code, problem)
    assert wrong.status_code == 200, wrong.text
    assert wrong.json()["verdict"] == "WA"
    assert wrong.json()["tests_passed"]["total"] == 3
    session = await ctx.client.post("/api/v1/sessions", headers=ctx.headers(),
                                    json={"submission_id": wrong.json()["id"]})
    assert session.status_code == 200, session.text
    session_id = session.json()["session_id"]
    claim = await ctx.client.post(f"/api/v1/sessions/{session_id}/messages", headers=ctx.headers(),
                                  json={"content": "改好了，AC了"})
    assert claim.json()["status"] != "resolved"
    corrected = wrong_code.replace("int n,x,s=0;", "int n,x;long long s=0;")
    accepted = await submit(ctx, corrected, problem)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["verdict"] == "AC"
    assert accepted.json()["tests_passed"]["scope"] == "package"
    verified = await ctx.client.post(f"/api/v1/sessions/{session_id}/verify", headers=ctx.headers(),
                                     json={"submission_id": accepted.json()["id"]})
    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "resolved"
    assert verified.json()["verified_submission_id"] == accepted.json()["id"]
