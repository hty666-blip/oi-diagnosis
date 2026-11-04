import os

import pytest

from app.capabilities import docker_runner
from app.capabilities.docker_runner import DockerRunner
from app.capabilities.sandbox import SandboxError
from app.services.submission_service import decide_verdict


@pytest.fixture
def runner(tmp_path, monkeypatch):
    monkeypatch.setattr(docker_runner, "RUN_ROOT", tmp_path / "runs")
    return DockerRunner(image=os.getenv("TEST_JUDGE_IMAGE", docker_runner.IMAGE))


def artifact(runner):
    work = runner.run_root / "job-test"
    work.mkdir()
    (work / "main").write_bytes(b"binary")
    return work


@pytest.mark.parametrize('raw,rc', [(None, 0), (b'', 0), (b'999', 0), (b'0\n', 0), (b'1', 0), (b'0', 1)])
async def test_missing_or_inconsistent_exit_evidence_cannot_be_accepted(runner, monkeypatch, raw, rc):
    work = artifact(runner)

    async def execute(work, command, **kwargs):
        if raw is not None:
            name = next(part for part in command if part.startswith('.exit-'))
            (work / name).write_bytes(raw)
        return rc, '', ''

    monkeypatch.setattr(runner, '_docker', execute)
    with pytest.raises(SandboxError, match='退出状态'):
        await runner.run(str(work))
    assert not list(work.glob('.exit-*'))


@pytest.mark.parametrize("language", ["c11", "cpp17", "java17", "python3"])
async def test_language_runtime_arguments_and_reserved_exit_code_stay_distinct(runner, monkeypatch, language):
    runner.set_language(language)
    work = runner.run_root / "job-language"
    work.mkdir()
    (work / runner.spec.artifact).write_bytes(b"artifact")
    literal = "space ; $(printf forged) ' \" end"

    async def execute(work, command, **kwargs):
        name = next(part for part in command if part.startswith(".exit-"))
        (work / name).write_text("125", encoding="ascii")
        offset = command.index(name) + 1
        assert command[offset:] == [*runner.spec.run_command(512), literal]
        return 1, "", ""

    monkeypatch.setattr(runner, "_docker", execute)
    result = await runner.run(str(work), extra_args=[literal])
    assert result.status == "Non Zero Exit Status" and result.exit_code == 125
    assert not list(work.glob(".exit-*"))


@pytest.mark.skipif(os.environ.get('RUN_DOCKER_TESTS') != '1', reason='需要显式启用真实Docker')
async def test_real_student_exit_codes_arguments_and_timeout_stay_distinct(runner):
    code = r'''
#include <cstdlib>
#include <iostream>
#include <string>
int main(int argc, char** argv) {
    if (argc > 1 && std::string(argv[1]) == "spin") { while (true) {} }
    if (argc > 2 && std::string(argv[1]) == "echo") { std::cout << argv[2]; return 0; }
    return argc > 1 ? std::atoi(argv[1]) : 0;
}
'''
    comp = await runner.compile(code)
    assert comp.ok
    try:
        for exit_code in (0, 1, 124, 125, 126, 127, 137, 139, 255):
            result = await runner.run(comp.artifact_id, extra_args=[str(exit_code)])
            assert result.exit_code == exit_code
            assert result.status == ('Accepted' if exit_code == 0 else 'Non Zero Exit Status')
            verdict = decide_verdict(comp, [({'output': ''}, result)], scope='package')
            assert verdict.verdict == ('AC' if exit_code == 0 else 'RE')
        literal = "space ; $(printf forged) ' \" end"
        echoed = await runner.run(comp.artifact_id, extra_args=['echo', literal])
        assert echoed.status == 'Accepted' and echoed.stdout == literal
        timed_out = await runner.run(comp.artifact_id, extra_args=['spin'], time_limit_ms=500)
        assert timed_out.status == 'Time Limit Exceeded'
        assert not list(runner._workdir(comp.artifact_id).glob('.exit-*'))
    finally:
        await runner.cleanup(comp.artifact_id)


@pytest.mark.skipif(os.environ.get("RUN_DOCKER_TESTS") != "1", reason="需要显式启用真实Docker")
@pytest.mark.parametrize(
    ("language", "code"),
    [
        ("c11", '#include <stdlib.h>\nint main(int argc,char**argv){return argc>1?atoi(argv[1]):0;}'),
        ("java17", 'public class Main { public static void main(String[] args) {'
                    ' System.exit(args.length > 0 ? Integer.parseInt(args[0]) : 0); } }'),
        ("python3", 'import sys\nsys.exit(int(sys.argv[1]) if len(sys.argv) > 1 else 0)\n'),
    ],
)
async def test_real_language_programs_preserve_student_exit_codes(runner, language, code):
    runner.set_language(language)
    comp = await runner.compile(code)
    assert comp.ok, comp.stderr
    try:
        for exit_code in (0, 124, 125, 126, 127, 137, 255):
            result = await runner.run(comp.artifact_id, extra_args=[str(exit_code)])
            assert result.exit_code == exit_code, (language, result)
            assert result.status == ("Accepted" if exit_code == 0 else "Non Zero Exit Status")
    finally:
        await runner.cleanup(comp.artifact_id)
