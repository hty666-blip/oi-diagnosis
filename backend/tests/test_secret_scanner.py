import importlib.util
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def scanner(tmp_path):
    location = Path(__file__).resolve().parents[2] / "tools" / "check_secrets.py"
    spec = importlib.util.spec_from_file_location("project_secret_scanner", location)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = tmp_path
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return module


def stage(scanner, name, content):
    path = scanner.ROOT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "--", name], cwd=scanner.ROOT, check=True,
                   capture_output=True)


def test_real_env_is_blocked_without_printing_its_content(scanner, capsys):
    private = "private-test-" + "x" * 30
    stage(scanner, ".env", "LLM_API_KEY=" + private)
    assert scanner.main([]) == 1
    output = capsys.readouterr().out
    assert ".env" in output
    assert private not in output


def test_copied_env_secret_in_source_is_blocked_without_echo(scanner, capsys):
    private = "unusual-provider-" + "x" * 30
    (scanner.ROOT / ".env").write_text("LLM_API_KEY=" + private, encoding="utf-8")
    stage(scanner, "app.py", "credential = " + repr(private))
    assert scanner.main([]) == 1
    output = capsys.readouterr().out
    assert "app.py" in output
    assert private not in output


def test_deleted_file_is_not_read_from_the_index(scanner):
    stage(scanner, "old.py", "print('safe')\n")
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "initial"], cwd=scanner.ROOT, check=True)
    subprocess.run(["git", "rm", "--", "old.py"], cwd=scanner.ROOT, check=True,
                   capture_output=True)
    assert scanner.main([]) == 0


def test_all_mode_checks_unchanged_tracked_secrets(scanner, capsys):
    private = "sk-" + "x" * 32
    stage(scanner, "old.py", "credential = " + repr(private))
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "initial"], cwd=scanner.ROOT, check=True)
    assert scanner.main([]) == 0
    assert scanner.main(["--all"]) == 1
    output = capsys.readouterr().out
    assert "old.py" in output
    assert private not in output


def test_public_example_and_dummy_key_are_allowed(scanner):
    stage(scanner, ".env.example", "LLM_API_KEY=sk-replace-me\n")
    stage(scanner, "app.py", "credential = 'sk-test'\n")
    assert scanner.main(["--all"]) == 0
