from app.capabilities.sandbox import CommandResult, CompileResult
from app.services.submission_service import decide_verdict, normalize_code, outputs_equal


def _run(stdout: str = "", status: str = "Accepted", exit_code: int = 0) -> CommandResult:
    return CommandResult(status=status, exit_code=exit_code, time_ns=1_000_000,
                         memory_bytes=1024, stdout=stdout)


COMP_OK = CompileResult(ok=True, stderr="", artifact_id="a1")


def test_outputs_equal_ignores_trailing_spaces_and_newlines():
    assert outputs_equal("3 \n", "3")
    assert outputs_equal("1\n2\n\n", "1\n2")
    assert not outputs_equal("3", "4")


def test_normalize_code_preserves_bom_line_numbers_and_string_whitespace():
    a = normalize_code("\ufeffint main(){\r\n  return 0;  \r\n}")
    b = "\ufeffint main(){\n  return 0;  \n}"
    assert a == b


def test_compile_error():
    r = decide_verdict(CompileResult(ok=False, stderr="error"), [])
    assert r.verdict == "CE"


def test_all_samples_pass():
    samples = [{"input": "1 2", "output": "3"}, {"input": "2 2", "output": "4"}]
    runs = [(samples[0], _run("3")), (samples[1], _run("4"))]
    r = decide_verdict(COMP_OK, runs)
    assert r.verdict == "SAMPLE_PASS"
    assert (r.passed, r.total) == (2, 2)


def test_wrong_answer():
    samples = [{"input": "1 2", "output": "3"}]
    r = decide_verdict(COMP_OK, [(samples[0], _run("4"))])
    assert r.verdict == "WA"
    assert r.passed == 0


def test_time_limit_takes_priority():
    samples = [{"input": "1", "output": "1"}, {"input": "2", "output": "2"}]
    runs = [(samples[0], _run("1")), (samples[1], _run("", status="Time Limit Exceeded"))]
    r = decide_verdict(COMP_OK, runs)
    assert r.verdict == "TLE"
    assert r.passed == 1


def test_runtime_error_on_nonzero_exit():
    samples = [{"input": "1", "output": "1"}]
    r = decide_verdict(COMP_OK, [(samples[0], _run("", exit_code=1))])
    assert r.verdict == "RE"


def test_no_samples_is_flagged():
    r = decide_verdict(COMP_OK, [])
    assert r.verdict == "NO_TESTS"
    assert r.detail == {"note": "no_tests"}


def test_only_nonempty_package_can_receive_ac():
    r = decide_verdict(COMP_OK, [({"input": "1", "output": "1"}, _run("1"))],
                       scope="package")
    assert r.verdict == "AC"
    assert r.scope == "package"
    assert decide_verdict(COMP_OK, [], scope="package").verdict == "NO_TESTS"


def test_internal_runner_failure_cannot_be_a_student_error():
    import pytest

    from app.capabilities.sandbox import SandboxError

    with pytest.raises(SandboxError):
        decide_verdict(COMP_OK, [({"output": ""}, _run(status="Internal Error"))])
