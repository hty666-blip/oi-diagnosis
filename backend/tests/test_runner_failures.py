import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from app.capabilities import docker_runner
from app.capabilities.docker_runner import DockerRunner
from app.capabilities.sandbox import SandboxClient, SandboxError


@pytest.fixture
def runner(tmp_path, monkeypatch):
    monkeypatch.setattr(docker_runner, "RUN_ROOT", tmp_path / "runs")
    return DockerRunner()


def artifact(runner):
    work = runner.run_root / "job-test"
    work.mkdir()
    (work / "main").write_bytes(b"binary")
    return work


@pytest.mark.parametrize("rc", [125, 126, 127])
async def test_docker_environment_failure_is_not_compile_error(runner, monkeypatch, rc):
    monkeypatch.setattr(runner, "_docker", AsyncMock(return_value=(rc, "", "daemon failed")))
    with pytest.raises(SandboxError, match="Docker"):
        await runner.compile("int main() {}")
    assert list(runner.run_root.iterdir()) == []


async def test_real_compile_error_keeps_diagnostic_and_removes_workdir(runner, monkeypatch):
    monkeypatch.setattr(runner, "_docker", AsyncMock(return_value=(1, "", "main.cpp: error")))
    result = await runner.compile("broken C++")
    assert not result.ok
    assert result.stderr == "main.cpp: error"
    assert list(runner.run_root.iterdir()) == []


@pytest.mark.parametrize("rc", [124, 137, 139])
async def test_compiler_timeout_or_signal_is_not_a_syntax_error(runner, monkeypatch, rc):
    monkeypatch.setattr(runner, "_docker", AsyncMock(return_value=(rc, "", "compiler stopped")))
    with pytest.raises(SandboxError, match="编译过程异常"):
        await runner.compile("int main() {}")
    assert list(runner.run_root.iterdir()) == []


async def test_compile_transport_failure_removes_workdir(runner, monkeypatch):
    monkeypatch.setattr(runner, "_docker", AsyncMock(side_effect=SandboxError("offline")))
    with pytest.raises(SandboxError, match="offline"):
        await runner.compile("int main() {}")
    assert list(runner.run_root.iterdir()) == []


async def test_missing_binary_after_success_is_executor_failure(runner, monkeypatch):
    monkeypatch.setattr(runner, "_docker", AsyncMock(return_value=(0, "", "")))
    with pytest.raises(SandboxError, match="可执行产物"):
        await runner.compile("int main() {}")
    assert list(runner.run_root.iterdir()) == []


@pytest.mark.parametrize("rc", [125, 126, 127])
async def test_docker_environment_failure_is_not_student_runtime_error(runner, monkeypatch, rc):
    work = artifact(runner)
    monkeypatch.setattr(runner, "_docker", AsyncMock(return_value=(rc, "", "container failed")))
    with pytest.raises(SandboxError, match="Docker"):
        await runner.run(str(work))


@pytest.mark.parametrize("output", ["12345", "中中"])
async def test_output_exceeding_byte_limit_cannot_be_accepted(runner, monkeypatch, output):
    work = artifact(runner)
    monkeypatch.setattr(runner, "_docker", AsyncMock(return_value=(0, output, "")))
    result = await runner.run(str(work), stdout_max=4)
    assert result.status == "Output Limit Exceeded"


async def test_sigkill_does_not_claim_proven_memory_limit(runner, monkeypatch):
    work = artifact(runner)
    monkeypatch.setattr(runner, "_docker", AsyncMock(return_value=(137, "", "")))
    assert (await runner.run(str(work))).status == "Signalled"


async def test_cleanup_refuses_root_and_unrelated_directory(runner, tmp_path):
    unrelated = tmp_path / "important"
    unrelated.mkdir()
    (unrelated / "keep.txt").write_text("keep", encoding="utf-8")
    for target in (runner.run_root, unrelated):
        with pytest.raises(SandboxError, match="不属于"):
            await runner.cleanup(str(target))
    assert (unrelated / "keep.txt").read_text(encoding="utf-8") == "keep"


async def test_compile_and_run_cleans_artifact_when_run_fails(runner, monkeypatch):
    async def compile_command(work, cmd, **kwargs):
        (work / "main").write_bytes(b"binary")
        return 0, "", ""

    monkeypatch.setattr(runner, "_docker", compile_command)
    monkeypatch.setattr(runner, "run", AsyncMock(side_effect=SandboxError("offline")))
    with pytest.raises(SandboxError, match="offline"):
        await runner.compile_and_run("int main() {}")
    assert list(runner.run_root.iterdir()) == []


class FakeProcess:
    def __init__(self, *, stdout=b"", pending=False):
        self.returncode = None if pending else 0
        self.killed = False
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdout.feed_data(stdout)
        if not pending:
            self.stdout.feed_eof()
            self.stderr.feed_eof()
        self.finished = asyncio.Event()
        if not pending:
            self.finished.set()

    def kill(self):
        self.killed = True
        self.returncode = -9
        self.finished.set()

    async def wait(self):
        await self.finished.wait()
        return self.returncode


@pytest.mark.parametrize("failure", ["timeout", "output", "cancel"])
async def test_aborted_docker_execution_kills_cli_and_its_container(runner, monkeypatch, failure):
    work = artifact(runner)
    process = FakeProcess(stdout=b"x" * 20 if failure == "output" else b"", pending=True)
    calls = []
    started = asyncio.Event()

    async def start(*args, **kwargs):
        calls.append(args)
        if args[1] == "run":
            started.set()
            return process
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)
    task = asyncio.create_task(runner._docker(work, ["./main"], timeout_s=0.01, stdout_max=4))
    if failure == "cancel":
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    elif failure == "timeout":
        with pytest.raises(SandboxError, match="超时"):
            await task
    else:
        result = await task
        assert result[0] == docker_runner.OUTPUT_LIMIT_EXIT
        assert result[1] == "xxxx"
    assert process.killed
    name = calls[0][calls[0].index("--name") + 1]
    assert calls[1] == ("docker", "rm", "-f", name)


def judge_client(handler):
    client = SandboxClient(base_url="http://judge")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://judge")
    return client


@pytest.mark.parametrize("status", ["Internal Error", "File Error", "Unknown"])
async def test_judge_system_status_is_retryable_sandbox_error(status):
    def handler(request):
        return httpx.Response(200, json=[{"status": status, "exitStatus": 0}])

    client = judge_client(handler)
    try:
        with pytest.raises(SandboxError, match="未完成执行"):
            await client.run("artifact")
    finally:
        await client.close()


@pytest.mark.parametrize("body", [[], {}, [None], [{"status": "Accepted"}], "not json"])
async def test_invalid_judge_response_cannot_look_successful(body):
    def handler(request):
        return httpx.Response(200, text=body) if isinstance(body, str) else httpx.Response(200, json=body)

    client = judge_client(handler)
    try:
        with pytest.raises(SandboxError, match="无效执行结果"):
            await client.run("artifact")
    finally:
        await client.close()


@pytest.mark.parametrize("stdout", ["1234", "YWJj", "中文输出\n"])
async def test_judge_never_decodes_base64_looking_plain_output(stdout):
    client = judge_client(lambda request: httpx.Response(200, json=[{
        "status": "Accepted", "exitStatus": 0, "files": {"stdout": stdout, "stderr": ""},
    }]))
    try:
        assert (await client.run("artifact")).stdout == stdout
    finally:
        await client.close()


async def test_judge_success_without_binary_is_infrastructure_failure():
    client = judge_client(lambda request: httpx.Response(200, json=[{
        "status": "Accepted", "exitStatus": 0, "files": {"stdout": "", "stderr": ""},
    }]))
    try:
        with pytest.raises(SandboxError, match="缺少可执行产物"):
            await client.compile("int main() {}")
    finally:
        await client.close()


async def test_judge_collector_error_cannot_become_accepted_empty_output():
    client = judge_client(lambda request: httpx.Response(200, json=[{
        "status": "Accepted", "exitStatus": 0, "files": {"stdout": "", "stderr": ""},
        "fileError": [{"name": "stdout", "type": "CopyOutOpen"}],
    }]))
    try:
        with pytest.raises(SandboxError, match="未完成执行"):
            await client.run("artifact")
    finally:
        await client.close()


async def test_judge_compile_and_run_releases_cached_binary_after_failure():
    methods = []

    def handler(request):
        methods.append((request.method, request.url.path))
        if request.method == "DELETE":
            return httpx.Response(200)
        if len(methods) == 1:
            return httpx.Response(200, json=[{
                "status": "Accepted", "exitStatus": 0, "files": {"stdout": "", "stderr": ""},
                "fileIds": {"main": "cached-id"},
            }])
        return httpx.Response(500)

    client = judge_client(handler)
    try:
        with pytest.raises(SandboxError):
            await client.compile_and_run("int main() {}")
        assert methods[-1] == ("DELETE", "/file/cached-id")
    finally:
        await client.close()
