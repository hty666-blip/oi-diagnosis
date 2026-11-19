"""多语言与版本链、缓存及持久任务合并后的交叉回归。"""

import os

import pytest
from sqlalchemy import select

from app.capabilities.docker_runner import IMAGE, DockerRunner
from app.capabilities.sandbox import CommandResult, CompileResult
from app.domain.models import JudgeJob, Problem


class LanguageRunner:
    selected = []

    def set_language(self, language):
        self.language = language
        self.selected.append(language)

    async def compile(self, code):
        return CompileResult(ok=True, stderr="", artifact_id="in-memory")

    async def run(self, artifact_id, **kwargs):
        return CommandResult(status="Accepted", exit_code=0, time_ns=1,
                             memory_bytes=1, stdout="1")


async def send(ctx, language, parent=None, *, background=False):
    headers = ctx.headers()
    if background:
        headers["Prefer"] = "respond-async"
    return await ctx.client.post("/api/v1/submissions", headers=headers, json={
        "problem_pid": ctx.problem.pid,
        "code": "int main(){}",
        "language": language,
        "parent_submission_id": parent,
    })


async def test_language_and_parent_jointly_scope_cached_submissions(api_context, monkeypatch):
    ctx = api_context
    LanguageRunner.selected = []
    monkeypatch.setattr("app.services.submission_service.build_runner", LanguageRunner)
    ctx.problem.samples = [{"input": "1", "output": "1"}]
    await ctx.session.commit()
    cpp = await send(ctx, "cpp17")
    assert cpp.status_code == 200, cpp.text
    cpp = cpp.json()
    c = (await send(ctx, "c11")).json()
    assert c["id"] != cpp["id"]
    child = (await send(ctx, "c11", cpp["id"])).json()
    cached = (await send(ctx, "c11", cpp["id"])).json()
    branch = (await send(ctx, "cpp17", cpp["id"])).json()
    assert child["id"] == cached["id"]
    assert len({cpp["id"], c["id"], child["id"], branch["id"]}) == 4
    assert LanguageRunner.selected == ["cpp17", "c11", "c11", "cpp17"]
    chain = await ctx.client.get(f"/api/v1/submissions/{child['id']}/lineage", headers=ctx.headers())
    assert chain.status_code == 200
    assert [item["language"] for item in chain.json()] == ["cpp17", "c11"]
    history = await ctx.client.get("/api/v1/me/submissions", headers=ctx.headers())
    assert {item["language"] for item in history.json()} == {"cpp17", "c11"}


async def test_multilingual_async_request_preserves_language_and_parent(api_context, monkeypatch):
    ctx = api_context

    class NeverCompile(LanguageRunner):
        async def compile(self, code):
            raise AssertionError("异步请求不能在接受时编译")

    monkeypatch.setattr("app.services.submission_service.build_runner", NeverCompile)
    parent = await ctx.submission(lang="cpp17")
    await ctx.session.commit()
    response = await send(ctx, "python3", parent.id, background=True)
    assert response.status_code == 202, response.text
    queued = response.json()
    assert queued["language"] == "python3"
    assert queued["parent_submission_id"] == parent.id
    assert queued["job"]["status"] == "queued"
    duplicate = await send(ctx, "python3", parent.id, background=True)
    assert duplicate.status_code == 202
    assert duplicate.json()["id"] == queued["id"]
    assert duplicate.json()["job"]["id"] == queued["job"]["id"]
    jobs = (await ctx.session.scalars(select(JudgeJob).where(
        JudgeJob.submission_id == queued["id"],
    ))).all()
    assert len(jobs) == 1


async def test_language_change_can_resolve_same_source_but_same_language_is_rejected(api_context):
    ctx = api_context
    code = '#include <stdio.h>\nint main(){printf("%zu\\n",sizeof(\'a\'));}'
    original = await ctx.submission(code=code, code_hash="same-source", lang="c11")
    opened = await ctx.client.post("/api/v1/sessions", headers=ctx.headers(),
                                   json={"submission_id": original.id})
    assert opened.status_code == 200, opened.text
    session_id = opened.json()["session_id"]
    counts = {"passed": 3, "total": 3, "scope": "package"}
    same_language = await ctx.submission(code=code, code_hash="same-source", lang="c11",
                                         parent_submission_id=original.id, verdict="AC",
                                         tests_passed=counts)
    rejected = await ctx.client.post(f"/api/v1/sessions/{session_id}/verify", headers=ctx.headers(),
                                     json={"submission_id": same_language.id})
    assert rejected.status_code == 409
    changed = await ctx.submission(code=code, code_hash="same-source", lang="cpp17",
                                   parent_submission_id=original.id, verdict="AC", tests_passed=counts)
    verified = await ctx.client.post(f"/api/v1/sessions/{session_id}/verify", headers=ctx.headers(),
                                     json={"submission_id": changed.id})
    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "resolved"
    assert verified.json()["verified_submission_id"] == changed.id


@pytest.mark.skipif(os.getenv("RUN_DOCKER_TESTS") != "1", reason="需要显式启用 Docker 判题集成")
@pytest.mark.parametrize(
    ("language", "code"),
    [
        ("cpp17", '#include <iostream>\nint main(){int n;long long x,s=0;std::cin>>n;'
                   'while(n--){std::cin>>x;s+=x;}std::cout<<s<<"\\n";}'),
        ("c11", '#include <stdio.h>\nint main(){int n;long long x,s=0;scanf("%d",&n);'
                 'while(n--){scanf("%lld",&x);s+=x;}printf("%lld\\n",s);return 0;}'),
        ("java17", 'import java.util.Scanner; public class Main {'
                    ' public static void main(String[] args) {Scanner in=new Scanner(System.in);'
                    ' int n=in.nextInt();System.out.println(Helper.sum(in,n));}}'
                    ' class Helper {static long sum(Scanner in,int n){long result=0;'
                    ' while(n-->0){result+=in.nextLong();}return result;}}'),
        ("python3", 'import sys\ndata=list(map(int,sys.stdin.buffer.read().split()))\n'
                     'print(sum(data[1:1+data[0]]))\n'),
    ],
)
async def test_real_multilingual_submission_runs_formal_package(api_context, monkeypatch, language, code):
    ctx = api_context
    monkeypatch.setattr("app.services.submission_service.build_runner",
                        lambda: DockerRunner(image=os.getenv("TEST_JUDGE_IMAGE", IMAGE)))
    problem = Problem(pid="PTV-0001", title="多语言求和验收", package_level="A",
                      has_testdata=True, samples=[])
    ctx.session.add(problem)
    await ctx.session.commit()
    response = await ctx.client.post("/api/v1/submissions", headers=ctx.headers(), json={
        "problem_pid": problem.pid, "language": language, "code": code,
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["language"] == language
    assert result["verdict"] == "AC", result
    assert result["tests_passed"]["passed"] == result["tests_passed"]["total"] == 3
    assert result["tests_passed"]["scope"] == "package"
