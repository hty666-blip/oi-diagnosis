import httpx
import pytest

from app.capabilities.sandbox import SandboxClient, SandboxError


def _item(**kw):
    base = {
        "status": "Accepted",
        "exitStatus": 0,
        "time": 1_500_000,
        "memory": 1024 * 1024,
        "files": {"stdout": "3\n", "stderr": ""},
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_compile_builds_expected_payload():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=[_item(fileIds={"main": "abc123"})])

    c = SandboxClient(base_url="http://judge")
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://judge")

    res = await c.compile("int main(){return 0;}")
    await c.close()

    assert res.ok is True
    assert res.artifact_id == "abc123"
    cmd = captured["cmd"][0]
    assert cmd["args"][0] == "/usr/bin/g++"
    assert "main.cpp" in cmd["copyIn"]
    assert cmd["copyOutCached"] == ["main?"]
    assert cmd["memoryLimit"] > 0
    assert cmd["files"][0] == {"content": ""}
    assert [file["name"] for file in cmd["files"][1:]] == ["stdout", "stderr"]
    assert set(captured) == {"cmd"}


@pytest.mark.asyncio
async def test_compile_failure_keeps_stderr():
    err = "main.cpp:1:1: error: bad"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_item(status="Non Zero Exit Status", exitStatus=1,
                                               files={"stdout": "", "stderr": err})])

    c = SandboxClient(base_url="http://judge")
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://judge")
    res = await c.compile("bad code")
    await c.close()

    assert res.ok is False
    assert "error: bad" in res.stderr


@pytest.mark.asyncio
async def test_run_requires_artifact():
    c = SandboxClient(base_url="http://judge")
    with pytest.raises(SandboxError):
        await c.run(None)


@pytest.mark.asyncio
async def test_run_sends_file_id_and_keeps_plain_stdout():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=[_item()])

    c = SandboxClient(base_url="http://judge")
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://judge")
    res = await c.run("abc123", stdin="1 2\n", time_limit_ms=1000, memory_limit_mb=256)
    await c.close()

    cmd = captured["cmd"][0]
    assert cmd["copyIn"]["main"] == {"fileId": "abc123"}
    assert cmd["files"][0] == {"content": "1 2\n"}
    assert [file["name"] for file in cmd["files"][1:]] == ["stdout", "stderr"]
    assert set(captured) == {"cmd"}
    assert res.stdout == "3\n"
    assert res.time_ms == 1


@pytest.mark.asyncio
async def test_http_error_becomes_sandbox_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    c = SandboxClient(base_url="http://judge")
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://judge")
    with pytest.raises(SandboxError):
        await c.compile("x")
    await c.close()
