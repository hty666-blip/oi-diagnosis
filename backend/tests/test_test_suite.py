from pathlib import Path
from types import SimpleNamespace

import pytest

from app.capabilities.sandbox import SandboxError
from app.capabilities.test_suite import PACKAGE_ROOT, load_package, load_suite


def problem(**kw):
    fields = {"pid": "P1001", "statement_md": "求和", "has_testdata": False,
              "package_level": "C", "samples": [{"input": "1 2", "output": "3"}]}
    return SimpleNamespace(**(fields | kw))


def test_sample_suite_is_not_a_formal_test_suite():
    suite = load_suite(problem())
    assert suite.scope == "samples"
    assert len(suite.cases) == 1


def test_own_package_loads_all_real_test_files():
    suite = load_suite(problem(pid="PTV-0001", package_level="A", has_testdata=True))
    assert suite.scope == "package"
    assert len(suite.cases) == 3
    assert suite.cases[1]["output"].strip() == "10000000000"
    assert suite.time_limit_ms == 1000


def test_changed_sample_answer_and_statement_invalidate_cache():
    original = load_suite(problem()).digest
    assert load_suite(problem(samples=[{"input": "1 2", "output": "4"}])).digest != original
    assert load_suite(problem(statement_md="修改后的题面")).digest != original


def test_declared_but_missing_package_fails_closed():
    with pytest.raises(SandboxError, match="不可用"):
        load_suite(problem(pid="MISSING", package_level="A", has_testdata=True))


@pytest.mark.parametrize("samples", [[{"input": "1"}], [{"input": 1, "output": "1"}], [None]])
def test_malformed_samples_cannot_be_silently_accepted(samples):
    with pytest.raises(SandboxError, match="标准输出"):
        load_suite(problem(samples=samples))


def test_unpaired_formal_tests_are_rejected(tmp_path):
    directory = tmp_path / "BROKEN"
    (directory / "tests").mkdir(parents=True)
    (directory / "meta.yaml").write_text(
        "id: BROKEN\npackage_level: B\nreviewer: teacher\n", encoding="utf-8"
    )
    (directory / "tests" / "01.in").write_text("1", encoding="utf-8")
    with pytest.raises(SandboxError, match="不成对"):
        load_package("BROKEN", tmp_path)


def test_package_path_cannot_escape_root():
    with pytest.raises(SandboxError, match="编号"):
        load_package("../outside", Path(PACKAGE_ROOT))
