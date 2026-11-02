"""复现已贴输出仍反复索要、误判索要代码及模型缺少上下文的问题。"""

import json

import httpx
import pytest
from sqlalchemy import select

from app.agents.compose import compose
from app.agents.tutor import DiagnosisView, SessionView, decide, reported_comparison
from app.domain.models import Evidence, HintSession, HintTurn, Mastery
from app.gateway.llm import LLMClient
from app.services.tutor_service import TutorService

REPORT = "预期输出\n-4\n实际输出\n2"
OLD_GATE = "这一步请先动手核对本次失败用例，把程序实际输出和预期输出贴出来。"


@pytest.mark.parametrize("message, expected, actual", [
    (REPORT, "-4", "2"),
    ("实际输出：2\n预期输出：-4", "-4", "2"),
    ("期望输出\n```\n-4\n```\n程序输出\n```\n2\n```", "-4", "2"),
    ("预期输出: 10000000000\n实际输出: 1000000000", "10000000000", "1000000000"),
    ("预期输出 -4 实际输出 2", "-4", "2"),
])
def test_reported_outputs_are_recognized_as_student_information(message, expected, actual):
    pair = reported_comparison(message)
    assert (pair.expected, pair.actual, pair.origin) == (expected, actual, "student")
    assert reported_comparison("请比较预期输出和实际输出") is None


@pytest.mark.parametrize("message", ["我贴出来了啊", "我已经发给你了", "帮我看看代码哪里有问题", "我把完整代码贴出来了"])
def test_providing_information_is_not_requesting_an_answer(message):
    history = (("student", REPORT), ("tutor", OLD_GATE))
    decision = decide(SessionView(level=3, history=history), message, DiagnosisView())
    assert decision.action == "explain"
    assert decision.mode == "compare"
    assert not decision.dependency_signal
    assert "-4" in decision.content and "2" in decision.content
    assert "贴出来" not in decision.content


def test_repeated_output_and_reminder_continue_the_discussion_without_failed_submission_quota():
    history = []
    replies = []
    level = 3
    for message in (REPORT, REPORT, "我已经贴过了啊"):
        decision = decide(SessionView(level=level, failed_attempts=0, history=tuple(history)),
                          message, DiagnosisView())
        assert decision.action == "explain" and decision.mode != "gate"
        assert "预期输出是 '-4'" in decision.content
        assert "实际输出是 '2'" in decision.content
        assert "贴出来" not in decision.content
        replies.append(decision.content)
        history.extend([("student", message), ("tutor", decision.content)])
        level = decision.level
    assert len(set(replies)) == 3
    assert level == 4


@pytest.mark.parametrize("bad_reply", [False, True], ids=["uses-context", "rejects-repeated-request"])
async def test_model_receives_code_and_history_and_cannot_request_the_same_outputs_again(bad_reply):
    captured = {}
    text = (OLD_GATE if bad_reply else
            "你提供的输出差异我已经看到了。先关注 sum 在循环中的更新，逐次记录它的值，再和手算的累计结果比较；还不能仅凭最终输出断定原因。")

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

    llm = LLMClient(base_url="https://model.test/v1", api_key="test-key", model="test",
                    transport=httpx.MockTransport(handler))
    history = (("student", REPORT), ("tutor", OLD_GATE))
    diag = DiagnosisView(source_code="for (int i=0;i<n;i++) { sum = x; }", problem_context="TEST 求和")
    decision = decide(SessionView(level=3, history=history), "我已经贴过了", diag)
    reply = await compose(decision, diag, "我已经贴过了", llm, history=history)
    prompt = captured["messages"][1]["content"]
    assert diag.source_code in prompt and diag.problem_context in prompt
    assert REPORT in prompt and OLD_GATE in prompt
    assert "学生自述（未独立核验）" in prompt
    assert llm.last_call and llm.last_call.ok
    assert reply == (decision.content if bad_reply else text)


async def test_existing_l3_session_uses_previous_outputs_without_rewriting_mastery_or_verdict(api_context):
    ctx = api_context
    submission = await ctx.submission()
    opened = await ctx.client.post("/api/v1/sessions", headers=ctx.headers(),
                                   json={"submission_id": submission.id})
    session_id = opened.json()["session_id"]
    hs = await ctx.session.get(HintSession, session_id)
    hs.level = 3
    ctx.session.add_all([
        HintTurn(session_id=session_id, role="student", level=3, content=REPORT),
        HintTurn(session_id=session_id, role="tutor", level=3, content=OLD_GATE),
    ])
    await ctx.session.flush()
    replies = []
    for message in (REPORT, "我贴出来了啊", "还是不太明白"):
        response = await ctx.client.post(f"/api/v1/sessions/{session_id}/messages", headers=ctx.headers(),
                                          json={"content": message})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["status"] == "open" and result["action"] == "explain"
        assert "-4" in result["reply"] and "2" in result["reply"]
        assert "贴出来" not in result["reply"]
        replies.append(result["reply"])
    assert hs.cheat_flags == 0 and hs.failed_attempts == 0 and hs.verified_submission_id is None
    assert submission.verdict == "WA"
    assert (await ctx.session.scalars(select(Mastery))).all() == []
    assert len(set(replies[:2])) == 2


@pytest.mark.parametrize("flags", [{}, {"stdout_truncated": True}, {"escaped_fields": ["/stdout"]}])
async def test_only_complete_plain_execution_outputs_are_explained_as_machine_evidence(api_context, flags):
    ctx = api_context
    submission = await ctx.submission()
    ctx.session.add(Evidence(submission_id=submission.id, type="run_trace", payload={
        "input": "3\n-5 -1 2\n", "stdout": "2\n", "expected": "-4\n",
        "status": "Accepted", "exit_code": 0, "matched": False, **flags,
    }))
    await ctx.session.flush()
    view = await TutorService(ctx.session, ctx.users.student)._diagnosis_view(None, submission.id)
    assert view.source_code == submission.code and ctx.problem.title in view.problem_context
    assert (view.comparison is not None) == (not flags)
    if not flags:
        decision = decide(SessionView(level=3), "还是不对啊", view)
        assert "执行记录中的" in decision.content and "-4" in decision.content


async def test_new_verified_version_does_not_reuse_previous_versions_reported_outputs(api_context):
    ctx = api_context
    original = await ctx.submission(code_hash="original")
    opened = await ctx.client.post("/api/v1/sessions", headers=ctx.headers(),
                                   json={"submission_id": original.id})
    session_id = opened.json()["session_id"]
    hs = await ctx.session.get(HintSession, session_id)
    hs.level = 3
    ctx.session.add(HintTurn(session_id=session_id, role="student", level=3, content=REPORT))
    await ctx.session.flush()
    changed = await ctx.submission(code_hash="changed")
    verified = await ctx.client.post(f"/api/v1/sessions/{session_id}/verify", headers=ctx.headers(),
                                      json={"submission_id": changed.id})
    assert verified.status_code == 200
    result = await ctx.client.post(f"/api/v1/sessions/{session_id}/messages", headers=ctx.headers(),
                                    json={"content": "继续检查"})
    assert result.status_code == 200
    assert "'-4'" not in result.json()["reply"]


@pytest.mark.parametrize("actor", ["student", "admin"])
async def test_invalid_historical_binding_cannot_feed_another_students_code_to_tutor(api_context, actor):
    ctx = api_context
    original = await ctx.submission()
    opened = await ctx.client.post("/api/v1/sessions", headers=ctx.headers(),
                                   json={"submission_id": original.id})
    hs = await ctx.session.get(HintSession, opened.json()["session_id"])
    foreign = await ctx.submission(ctx.users.peer)
    hs.verified_submission_id = foreign.id
    await ctx.session.flush()
    result = await ctx.client.post(f"/api/v1/sessions/{hs.id}/messages",
                                    headers=ctx.headers(getattr(ctx.users, actor)),
                                    json={"content": REPORT})
    assert result.status_code == (404 if actor == "student" else 409)
