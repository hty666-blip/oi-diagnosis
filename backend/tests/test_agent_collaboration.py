"""用离线模型协议验证真正的角色交接、独立反驳和基于反馈的修订。"""

import asyncio
import json

import httpx
import pytest
from sqlalchemy import select

from app.agents.collaboration import EVIDENCE_SYSTEM, REVIEW_SYSTEM
from app.domain.models import Diagnosis, Evidence
from app.gateway.llm import LLMClient
from app.orchestrator.graph import run_diagnosis

CODE = "int main(){int sum=0,x; while(cin>>x){sum=x;} cout<<sum;}"
RECORD = {"id": 7, "type": "run_trace", "payload": {"status": "Accepted", "exit_code": 0,
    "input": "-5 -1 2", "stdout": "2", "expected": "-4", "matched": False}}


def fake_team(*, decisions=("approve",), bad_evidence=False, fail_review=False, evidence_id=7):
    calls = []
    diagnoses = []
    reviews = 0

    async def respond(request):
        nonlocal reviews
        payload = json.loads(request.content)
        system, user = [message["content"] for message in payload["messages"]]
        if system == EVIDENCE_SYSTEM:
            role = "evidence"
            result = {"observations": [{"summary": "实际输出为最后一个输入2，预期是累加结果-4。",
                                         "evidence_ids": [999 if bad_evidence else evidence_id]}], "missing_information": []}
        elif system == REVIEW_SYSTEM:
            role = "critic"
            if fail_review:
                calls.append((role, user))
                return httpx.Response(503)
            decision = decisions[min(reviews, len(decisions) - 1)]
            reviews += 1
            result = {"decision": decision, "summary": "应检查赋值覆盖，已有小整数输入不支持溢出假设。" if decision != "approve" else "解释与赋值语义和已有执行差异一致，但仍需修改后正式测试。",
                      "evidence_ids": [evidence_id], "issues": [] if decision == "approve" else ["输入均是小整数，请重查sum=x覆盖了之前的累计值。"]}
        else:
            role = "diagnosis"
            diagnoses.append(user)
            result = {"mechanism": [{"code": "M04", "confidence": 0.5, "evidence_ids": [evidence_id],
                         "explain": "需要检查累计变量是否被当前输入覆盖。" if len(diagnoses) > 1 else "可能是整数范围导致结果异常。"}],
                      "narrative": "候选错因待核对。"}
        calls.append((role, user))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result, ensure_ascii=False)}}],
                                         "usage": {"prompt_tokens": 100, "completion_tokens": 30}})

    client = LLMClient(base_url="https://team.invalid/v1", api_key="offline-fake-key", model="test-team",
                       transport=httpx.MockTransport(respond))
    return client, calls, diagnoses


async def test_roles_handoff_independent_rejection_and_feedback_revision():
    client, calls, diagnoses = fake_team(decisions=("revise", "approve"))
    observed = []

    async def observe(role, used, reason):
        observed.append((role, used, reason))

    out = await run_diagnosis(submission_id=1, code=CODE, verdict="WA", evidence=[RECORD], llm=client, observer=observe)
    assert [role for role, _ in calls] == ["evidence", "diagnosis", "critic", "diagnosis", "critic"]
    assert "evidence_agent" in diagnoses[0] and "预期是累加结果-4" in diagnoses[0]
    assert "输入均是小整数" in diagnoses[1] and "上一轮候选解释" in diagnoses[1]
    assert "被当前输入覆盖" in out["draft"].narrative
    assert out["collaboration"]["mode"] == "multi_agent" and out["attempts"] == 2
    assert out["status"] == "needs_human" and out["draft"].need_human
    assert all(used and not reason for _, used, reason in observed)
    assert [s["status"] for s in out["collaboration"]["steps"] if s["agent"] == "critic"] == ["revise", "approve"]


async def test_two_rejections_stop_without_repeating_forever():
    client, calls, _ = fake_team(decisions=("revise", "revise"))
    out = await run_diagnosis(submission_id=1, code=CODE, verdict="WA", evidence=[RECORD], llm=client)
    assert len(calls) == 5 and out["attempts"] == 2
    assert out["collaboration"]["mode"] == "degraded"
    assert not out["draft"].mechanism and "critic_blocked" in out["draft"].degraded


@pytest.mark.parametrize("options", [{"fail_review": True}, {"decisions": ("uncertain",)}])
async def test_unavailable_or_uncertain_independent_review_blocks_candidate(options):
    client, calls, _ = fake_team(**options)
    out = await run_diagnosis(submission_id=1, code=CODE, verdict="WA", evidence=[RECORD], llm=client)
    assert len(calls) == 3 and out["attempts"] == 1
    assert out["status"] == "needs_human" and not out["draft"].mechanism
    assert out["collaboration"]["mode"] == "degraded"


async def test_fabricated_evidence_analysis_is_not_handed_to_diagnoser():
    client, _, diagnoses = fake_team(bad_evidence=True)
    out = await run_diagnosis(submission_id=1, code=CODE, verdict="WA", evidence=[RECORD], llm=client)
    assert '"evidence_agent": {}' in diagnoses[0]
    first = out["collaboration"]["steps"][0]
    assert first["status"] == "unavailable" and 999 not in first["evidence_ids"]


@pytest.mark.parametrize("verdict", ["AC", "SAMPLE_PASS", "NO_TESTS"])
async def test_success_or_missing_tests_do_not_invent_multi_agent_calls(verdict):
    client, calls, _ = fake_team()
    out = await run_diagnosis(submission_id=1, code="", verdict=verdict, evidence=[], llm=client)
    assert not calls and out["collaboration"]["mode"] == "rules"


async def test_disabled_models_are_labelled_rules_not_ai_collaboration():
    out = await run_diagnosis(submission_id=1, code=CODE, verdict="WA", evidence=[RECORD], llm=None)
    assert out["collaboration"]["mode"] == "rules"
    assert all(step["method"] == "rules" for step in out["collaboration"]["steps"])


async def test_slow_agent_is_bounded_and_does_not_leave_request_hanging(monkeypatch):
    monkeypatch.setattr("app.orchestrator.graph.AGENT_TIMEOUT_SECONDS", 0.002)

    async def slow(request):
        await asyncio.sleep(1)
        raise AssertionError("应已超时取消")

    client = LLMClient(base_url="https://team.invalid/v1", api_key="fake", model="fake", transport=httpx.MockTransport(slow))
    out = await run_diagnosis(submission_id=1, code=CODE, verdict="WA", evidence=[RECORD], llm=client)
    assert out["collaboration"]["mode"] == "degraded" and not out["draft"].mechanism


async def test_trace_is_saved_visible_to_owner_and_reused_without_new_model_calls(api_context, monkeypatch):
    ctx = api_context
    sub = await ctx.submission(code=CODE)
    record = Evidence(submission_id=sub.id, type="run_trace", payload=RECORD["payload"], digest="team-record")
    ctx.session.add(record)
    await ctx.session.flush()
    client, calls, _ = fake_team(evidence_id=record.id)

    async def configured(session):
        return client

    monkeypatch.setattr("app.api.v1.submissions.runtime_client", configured)
    url = f"/api/v1/submissions/{sub.id}/diagnose"
    first = await ctx.client.post(url, headers=ctx.headers())
    assert first.status_code == 200, first.text
    assert first.json()["collaboration"]["mode"] == "multi_agent"
    second = await ctx.client.post(url, headers=ctx.headers())
    assert second.status_code == 200 and len(calls) == 3
    assert second.json()["review_revision"] == first.json()["review_revision"]
    history = await ctx.client.get(f"/api/v1/submissions/{sub.id}", headers=ctx.headers())
    assert history.json()["diagnosis"]["collaboration"] == first.json()["collaboration"]
    assert (await ctx.client.get(f"/api/v1/submissions/{sub.id}", headers=ctx.headers(ctx.users.peer))).status_code == 404
    diagnosis = await ctx.session.scalar(select(Diagnosis).where(Diagnosis.submission_id == sub.id))
    assert diagnosis.model_meta["engine"] == "collaboration-v1"
