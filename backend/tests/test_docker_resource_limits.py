import os
from unittest.mock import AsyncMock

import pytest

from app.capabilities import docker_runner
from app.capabilities.docker_runner import DockerRunner


@pytest.fixture
def limited_runner(tmp_path, monkeypatch):
    monkeypatch.setattr(docker_runner, "RUN_ROOT", tmp_path / "runs")
    return DockerRunner(image=os.environ.get("TEST_JUDGE_IMAGE", docker_runner.IMAGE))


async def test_only_docker_oom_state_proves_mle(limited_runner, monkeypatch):
    runner = limited_runner
    work = runner.run_root / "job-oom"
    work.mkdir()
    (work / "main").write_bytes(b"binary")

    async def oom(*args, **kwargs):
        runner.last_state = {"OOMKilled": True, "StartedAt": "2026-11-04T00:00:00Z", "FinishedAt": "2026-11-04T00:00:01.250Z"}
        return 137, "", ""

    monkeypatch.setattr(runner, "_docker", oom)
    result = await runner.run(str(work))
    assert result.status == "Memory Limit Exceeded"
    assert result.time_ms == 1250 and result.time_kind == "container_wall"
    monkeypatch.setattr(runner, "_docker", AsyncMock(return_value=(137, "", "")))
    assert (await runner.run(str(work))).status == "Signalled"


@pytest.mark.skipif(os.environ.get("RUN_DOCKER_TESTS") != "1", reason="需要显式启用真实Docker")
async def test_real_program_has_nonroot_readonly_workdir_and_file_limits(limited_runner):
    code = r'''
#include <fstream>
#include <iostream>
#include <unistd.h>
#include <sys/resource.h>
int main(){
  std::ofstream host("host-write-probe");
  std::ofstream scratch("/tmp/scratch-probe"); scratch << "ok";
  rlimit size{}, core{}; getrlimit(RLIMIT_FSIZE,&size); getrlimit(RLIMIT_CORE,&core);
  std::cout << getuid() << " " << host.good() << " " << scratch.good() << " " << size.rlim_cur << " " << core.rlim_cur;
}
'''
    comp = await limited_runner.compile(code)
    assert comp.ok, comp.stderr
    try:
        result = await limited_runner.run(comp.artifact_id, time_limit_ms=2000, memory_limit_mb=64)
        assert result.status == "Accepted", result.stderr
        assert result.stdout == "65534 0 1 16777216 0"
        assert result.time_ns > 0 and result.time_kind == "container_wall"
        assert not (limited_runner._workdir(comp.artifact_id) / "host-write-probe").exists()
    finally:
        await limited_runner.cleanup(comp.artifact_id)


@pytest.mark.skipif(os.environ.get("RUN_DOCKER_TESTS") != "1", reason="需要显式启用真实Docker")
async def test_real_container_oom_is_mle_and_state_is_cleaned(limited_runner):
    code = r'''
#include <cstdlib>
#include <cstring>
#include <vector>
int main(){std::vector<void*> blocks; for(;;){void* p=malloc(1024*1024); if(!p)return 42; memset(p,1,1024*1024); blocks.push_back(p);}}
'''
    comp = await limited_runner.compile(code)
    assert comp.ok, comp.stderr
    try:
        result = await limited_runner.run(comp.artifact_id, time_limit_ms=5000, memory_limit_mb=48)
        assert result.status == "Memory Limit Exceeded", result
        assert limited_runner.last_state["OOMKilled"] is True
    finally:
        await limited_runner.cleanup(comp.artifact_id)
