"""有代码就直接教代码，反复不会不等于索要答案或停止辅导。"""

import json

import httpx
import pytest
from sqlalchemy import select

from app.agents.compose import compose
from app.agents.tutor import DiagnosisView, SessionView, decide, safe_output
from app.domain.models import Evidence, HintSession, Mastery
from app.gateway.llm import LLMClient
from app.services.tutor_service import TutorService

CODE = """#include <iostream>
int main() {
    int n;
    std::cin >> n;
    long long sum = 0, x;
    for (int i = 0; i < n; ++i) {
        std::cin >> x;
        sum = x;
    }
    std::cout << sum;
}
"""


def test_opening_and_repeated_confusion_read_the_code_without_escalating_or_requiring_failed_runs():
    diag = DiagnosisView(source_code=CODE, verdict="WA", problem_context="读入若干整数并求和")
    history = []
    detail = []
    for index, message in enumerate(("", "我不会", "还是不会", "我还是看不懂", "请直接讲代码怎么改")):
        d = decide(SessionView(level=0, failed_attempts=0, stuck_count=max(index - 1, 0),
                               history=tuple(history)), message, diag)
        assert d.action == "explain" and d.mode == "code"
        assert not d.dependency_signal and safe_output(d.content)
        assert "先动手" not in d.content and "交给老师" not in d.content
        detail.append(d.detail)
        history.extend([("student", message), ("tutor", d.content)])
    assert detail[:3] == [0, 1, 2]
    assert detail[-1] == 2


@pytest.mark.parametrize("bad", [None, "invented-line", "complete-program", "generic-question"])
async def test_direct_explanation_can_quote_real_lines_and_give_local_fix_at_the_first_level(bad):
    good = ("第 8 行的 `sum = x;` 是赋值，每读一个数都会覆盖旧的累计值，所以最后只剩最后一个输入。"
            "例如这次输入的末尾是 2，实际输出也就成了 2。局部应改为 `sum += x;`，它会在旧值基础上累加。"
            "这个建议需要重新提交验证，不等于程序已经通过测试。")
    text = {None: good, "invented-line": "第 999 行一定写错了，你直接修改那个位置即可，不用检查其他地方。",
            "complete-program": "完整程序如下：int main(){return 0;} 这样就可以直接提交。",
            "generic-question": "你先自己说说解题思路，再动手核对输出，想一想是哪里出了问题。"}[bad]
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

    diag = DiagnosisView(source_code=CODE, verdict="WA", problem_context="求和题")
    d = decide(SessionView(level=0), "我不会，直接讲这一句代码", diag)
    llm = LLMClient(base_url="https://model.test/v1", api_key="test-key", model="test",
                    transport=httpx.MockTransport(handler))
    reply = await compose(d, diag, "我不会，直接讲这一句代码", llm)
    prompt = captured["messages"][1]["content"]
    assert "8:         sum = x;" in prompt and "当前执行判定=WA" in prompt
    assert "不受猜测层级限制" in prompt and captured["max_tokens"] >= 1000
    if bad:
        assert "未获得可用的 AI 讲解" in reply and reply != text
    else:
        assert reply == good


@pytest.mark.parametrize("language, source", [
    ("cpp17", "long long sum = 0;\nsum = x;"),
    ("c11", "long long sum = 0;\nsum = x;"),
    ("java17", "long sum = 0;\nsum = x;"),
    ("python3", "total = 0\ntotal = x"),
])
def test_code_coaching_is_available_for_each_submission_language(language, source):
    decision = decide(SessionView(level=0), "直接解释每一行", DiagnosisView(language=language, source_code=source))
    assert decision.mode == "code" and decision.detail == 2


async def test_opening_and_repeated_confusion_use_model_with_increasing_detail(api_context, monkeypatch):
    ctx = api_context
    submission = await ctx.submission(code=CODE)
    captured = []

    class Model:
        available = True

        async def chat_text(self, system, prompt, **kwargs):
            captured.append(prompt)
            return "先看 `sum = x;`，这会覆盖 sum 的旧值，因此循环结束后只保留最后一个数，和累加所有输入的题意不符。"

    monkeypatch.setattr("app.services.tutor_service.LLMClient", Model)
    monkeypatch.setattr("app.core.config.settings.llm_enabled", True)
    result = await ctx.client.post("/api/v1/sessions", headers=ctx.headers(),
                                    json={"submission_id": submission.id})
    assert result.status_code == 200, result.text
    assert captured and "sum = x;" in captured[0]
    assert "覆盖" in result.json()["reply"] and "打算怎么解决" not in result.json()["reply"]
    session_id = result.json()["session_id"]
    for message in ("我不会", "还是不会"):
        reply = await ctx.client.post(f"/api/v1/sessions/{session_id}/messages", headers=ctx.headers(),
                                      json={"content": message})
        assert reply.status_code == 200 and reply.json()["status"] == "open"
    assert "学生说不会，先解释" in captured[1]
    assert "逐句拆解关键语句" in captured[2]


async def test_verified_session_can_continue_explanation_without_resetting_the_verified_result(api_context):
    ctx = api_context
    original = await ctx.submission(code=CODE, code_hash="wrong")
    opened = await ctx.client.post("/api/v1/sessions", headers=ctx.headers(),
                                   json={"submission_id": original.id})
    session_id = opened.json()["session_id"]
    passing = await ctx.submission(code=CODE.replace("sum = x;", "sum += x;"), code_hash="fixed",
                                   verdict="AC", tests_passed={"scope": "package", "passed": 3, "total": 3})
    verified = await ctx.client.post(f"/api/v1/sessions/{session_id}/verify", headers=ctx.headers(),
                                      json={"submission_id": passing.id})
    assert verified.json()["status"] == "resolved"
    for message in ("我不会，为什么这样改", "还是不会，请逐句讲", "我还是看不懂"):
        result = await ctx.client.post(f"/api/v1/sessions/{session_id}/messages", headers=ctx.headers(),
                                        json={"content": message})
        assert result.status_code == 200, result.text
        assert result.json()["action"] == "explain" and result.json()["status"] == "resolved"
        assert result.json()["verified_submission_id"] == passing.id
        assert result.json()["verdict"] == "AC" and "sum" in result.json()["reply"]
    hs = await ctx.session.get(HintSession, session_id)
    assert hs.status == "resolved" and hs.cheat_flags == 0 and hs.failed_attempts == 0
    assert (await ctx.session.scalars(select(Mastery))).all() == []


async def test_compile_error_context_is_available_without_claiming_a_run_output(api_context):
    ctx = api_context
    submission = await ctx.submission(code="int main(){ int n return 0; }", verdict="CE")
    ctx.session.add(Evidence(submission_id=submission.id, type="compile_log", payload={
        "ok": False, "stderr": "error: expected ';' before 'return'",
    }))
    await ctx.session.flush()
    view = await TutorService(ctx.session, ctx.users.student)._diagnosis_view(None, submission.id)
    assert view.verdict == "CE" and "expected ';'" in view.execution_context
    assert "尚未运行测试" in view.execution_context and view.comparison is None


def test_entry_point_names_can_be_explained_without_allowing_complete_entry_point_bodies():
    assert safe_output("`int main()` 是 C++ 程序的入口，函数体里的语句按顺序执行。")
    assert safe_output("`public static void main(String[] args)` 是 Java 程序的入口。")
    assert not safe_output("int main(){return 0;}")
    assert not safe_output("public static void main(String[] args) throws Exception {}")


@pytest.mark.parametrize("source, changed", [
    ('int main(){ std::cout << "a b"; }', 'int main(){ std::cout << "ab"; }'),
    (CODE, CODE.replace("sum = x;", "sum += x;")),
    ("if __name__ == '__main__':\n    if False:\n        print('a')\n        print('b')", "if __name__ == '__main__':\n    if False:\n        print('a')\n    print('b')"),
])
def test_existing_program_can_be_quoted_but_a_changed_complete_program_is_not_original_text(source, changed):
    quoted = "以下是你原有的代码，我只解释它的含义：\n```text\n" + source + "\n```"
    assert safe_output(quoted, source_code=source)
    assert not safe_output(quoted.replace(source, changed), source_code=source)
    assert not safe_output(quoted)


async def test_explaining_the_students_original_main_function_is_not_silently_discarded():
    text = f"先引用你原有的代码：\n```cpp\n{CODE}\n```\n这里的 `sum = x;` 会覆盖 sum 的旧值，循环结束后只剩最后一个数；局部改动应围绕累计更新来做。"
    llm = LLMClient(base_url="https://model.test/v1", api_key="test-key", model="test",
                    transport=httpx.MockTransport(lambda request: httpx.Response(
                        200, json={"choices": [{"message": {"content": text}}]},
                    )))
    diag = DiagnosisView(source_code=CODE, verdict="WA")
    decision = decide(SessionView(level=0), "请逐句讲代码", diag)
    assert await compose(decision, diag, "请逐句讲代码", llm) == text
