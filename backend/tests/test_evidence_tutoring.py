import json

import httpx
import pytest

from app.agents.compose import compose
from app.agents.contracts import DiagnosisDraft
from app.agents.critic import review
from app.agents.diagnostician import diagnose, llm_hypothesis
from app.agents.tutor import DiagnosisView, SessionView, TutorDecision, decide, safe_output
from app.gateway.llm import LLMClient


def client(handler):
    return LLMClient(base_url="https://model.test/v1", api_key="test-key", model="test",
                     transport=httpx.MockTransport(handler))


def response(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def failure(actual="4\n", expected="3\n"):
    return [{"id": 7, "type": "run_trace", "payload": {
        "status": "Accepted", "exit_code": 0, "input": "1 2\n",
        "stdout": actual, "expected": expected,
    }}]


def test_wa_reports_observed_difference_without_inventing_root_cause():
    draft = diagnose(failure(), "WA")
    assert "1 2" in draft.narrative
    assert "实际输出='4" in draft.narrative
    assert "预期输出='3" in draft.narrative
    assert "证据 #7" in draft.narrative
    assert not draft.mechanism and not draft.nature
    assert "溢出" not in draft.narrative
    assert "最小反例" not in draft.narrative
    assert draft.need_human


def test_difference_observation_uses_same_whitespace_semantics_as_judging():
    draft = diagnose(failure("3 \r\n\r\n", "3\n"), "WA")
    assert "实际输出=" not in draft.narrative


def test_unknown_location_and_failure_input_stay_unknown():
    diag = DiagnosisView()
    location = decide(SessionView(level=1), "我先检查计算", diag)
    assert location.level == 2
    assert "没有定位" in location.content
    assert "核心那几行" not in location.content
    case = decide(SessionView(level=2), "我把中间结果记下来了", diag)
    assert case.level == 3
    assert "没有可用" in case.content
    assert "1 个元素" not in case.content


def test_real_failure_input_is_not_claimed_to_be_minimal():
    diag = DiagnosisView(counterexample="1 2\n")
    decision = decide(SessionView(level=2), "我对比了输出", diag)
    assert "1 2" in decision.content
    assert "尚未最小化" in decision.content
    assert "最小反例" not in decision.content


def test_escalation_does_not_claim_teacher_was_notified():
    decision = decide(SessionView(level=3, stuck_count=2), "还是不会", DiagnosisView())
    assert decision.action == "escalate"
    assert "建议" in decision.content
    assert "同步给老师" not in decision.content and "整理给老师了" not in decision.content


@pytest.mark.parametrize("level", [0, 1, 2])
async def test_early_prompt_does_not_receive_l4_fix_or_full_diagnostic_message(level):
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return response("请先描述这段程序原本打算完成的计算，再把你的预期与运行记录比较一下。")

    diag = DiagnosisView(code="M01", line=8, message="sum溢出，把sum改为long long",
                         hint="把 sum 改成 long long")
    decision = TutorDecision(action="hint", level=level, content="先说说你打算怎么解决这道题。")
    await compose(decision, diag, "我先看看计算", client(handler))
    prompt = captured["messages"][1]["content"]
    assert diag.hint not in prompt
    assert diag.message not in prompt
    assert "long long" not in prompt
    if level < 2:
        assert "第 8 行" not in prompt


@pytest.mark.parametrize("bad_content", [None, [], {"text": "bad"}, 32])
async def test_bad_model_content_type_falls_back_to_template(bad_content):
    llm = client(lambda request: response(bad_content))
    decision = TutorDecision(action="hint", level=2, content="请手算这次失败输入，找出最早出现差异的一步。")
    assert await compose(decision, DiagnosisView(), "我不确定输出", llm) == decision.content
    assert llm.last_call and not llm.last_call.ok


async def test_model_timeout_keeps_tutoring_response_available():
    def handler(request):
        raise httpx.ReadTimeout("test timeout", request=request)

    llm = client(handler)
    decision = TutorDecision(action="explain", level=2, content="请手算这次失败输入，比较每一步的中间结果。")
    assert await compose(decision, DiagnosisView(), "我不确定输出", llm) == decision.content


@pytest.mark.parametrize("text", [
    "下面是一份完整答案，请直接提交：int main(){return 0;} 这样就完成了。",
    "下面是一份完整答案，请直接提交：signed main(){return 0;} 这样就完成了。",
    "我已经帮你定位好了，第 18 行就是根本原因，你从这一行开始检查即可。",
    "这是我找出的最小反例，你直接拿这组数据去运行就一定可以发现原因了。",
])
async def test_unsupported_model_claims_fall_back(text):
    decision = TutorDecision(action="hint", level=3, content="请先对照已经保存的失败输入与输出。")
    assert await compose(decision, DiagnosisView(), "继续", client(lambda request: response(text))) == decision.content


def test_full_program_guard_does_not_require_fences_or_block_a_single_statement():
    assert not safe_output("int main(){return 0;}")
    assert not review(DiagnosisDraft(narrative="signed main(){return 0;}"), set()).passed
    assert safe_output("可以检查这条语句：`sum += value;`。")


@pytest.mark.parametrize("field,value", [
    ("code", []), ("code", {}), ("confidence", float("nan")),
    ("confidence", float("inf")), ("confidence", True), ("confidence", []),
    ("confidence", 10 ** 1000),
    ("explain", {"bad": "value"}), ("evidence_ids", [7, 999]),
    ("explain", "已经请老师确认是溢出错误"), ("explain", "这是最小反例"),
])
async def test_malformed_hypothesis_fields_fail_closed(field, value):
    claim = {"code": "M04", "confidence": 0.5, "evidence_ids": [7], "explain": "需要核对边界"}
    claim[field] = value
    content = json.dumps({"mechanism": [claim], "narrative": "还需要继续核对"})
    draft = diagnose(failure(), "WA")
    assert await llm_hypothesis(draft, failure(), "WA", "", client(lambda request: response(content))) is None


async def test_hypothesis_preserves_execution_facts_and_never_claims_human_dispatch():
    content = json.dumps({"mechanism": [{"code": "M04", "confidence": 0.5,
                                         "evidence_ids": [7], "explain": "需要核对边界"}],
                          "narrative": "已请老师确认，肯定是边界写错了。"})
    draft = diagnose(failure(), "WA")
    result = await llm_hypothesis(draft, failure(), "WA", "", client(lambda request: response(content)))
    assert result is not None
    assert draft.narrative in result.narrative
    assert "待核对的模型假设" in result.narrative
    assert "已请老师确认" not in result.narrative
    assert result.need_human


async def test_invalid_json_is_recorded_as_failed_model_call():
    llm = client(lambda request: response("not JSON"))
    assert await llm.chat_json("s", "u") is None
    assert llm.last_call and not llm.last_call.ok


async def test_model_error_body_is_not_echoed_to_logs(caplog):
    llm = client(lambda request: httpx.Response(401, text="secret-test-api-key"))
    assert await llm.chat_text("s", "u") is None
    assert "secret-test-api-key" not in caplog.text
