import pytest

from app.agents.tutor import safe_output
from app.api.v1.submissions import SubmissionCreate
from app.capabilities.languages import language_spec
from app.capabilities.triage import triage


@pytest.mark.parametrize(
    ("language", "filename", "artifact", "compiler", "runtime"),
    [
        ("cpp17", "main.cpp", "main", "/usr/bin/g++", "./main"),
        ("c11", "main.c", "main", "/usr/bin/gcc", "./main"),
        ("java17", "Main.java", "main.jar", "/bin/bash", "/usr/bin/java"),
        ("python3", "main.py", "main.py", "/usr/bin/python3", "/usr/bin/python3"),
    ],
)
def test_language_commands(language, filename, artifact, compiler, runtime):
    spec = language_spec(language)
    assert spec.filename == filename
    assert spec.artifact == artifact
    assert spec.compile_command()[0] == compiler
    assert spec.run_command(512)[0] == runtime


def test_submission_language_is_validated():
    assert SubmissionCreate(problem_pid="P1", code="print(1)", language="python3").language == "python3"
    with pytest.raises(ValueError):
        SubmissionCreate(problem_pid="P1", code="x", language="javascript")


def test_python_and_java_compile_errors_receive_actionable_hints():
    python = triage(
        "if True\n    print(1)\n",
        language="python3",
        verdict="CE",
        compile_stderr=(
            '  File "main.py", line 1\n    if True\n           ^\n'
            "SyntaxError: expected ':'\n"
        ),
    )
    assert python.route == "syntax_help"
    assert python.top and python.top.line == 1
    assert "Python" in python.top.message

    java = triage(
        "public class Main {}",
        language="java17",
        verdict="CE",
        compile_stderr="Main.java:3: error: ';' expected\n",
    )
    assert java.route == "syntax_help"
    assert java.top and java.top.line == 3


@pytest.mark.parametrize(
    "text",
    [
        "```java\npublic static void main(String[] args) {}\n```",
        "```python\nif __name__ == '__main__':\n    print(input())\n```",
    ],
)
def test_safe_output_blocks_complete_programs_in_all_languages(text):
    assert not safe_output(text)
