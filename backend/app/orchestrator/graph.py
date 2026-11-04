"""有交接材料、独立审查和限次修订的多 Agent 协作图。

证据分析 → 诊断 → 独立审查 →（具体意见，最多再诊断一次）→ 仲裁。
判题由外部执行器完成；模型通过审查也不等于正式测试通过或教师审定。
"""

from __future__ import annotations

import asyncio
import time
from typing import TypedDict

from langgraph.graph import END, StateGraph

from app.agents.collaboration import analyze_evidence, evidence_packet, review_diagnosis
from app.agents.contracts import Critique, DiagnosisDraft
from app.agents.critic import review
from app.agents.diagnostician import diagnose, llm_hypothesis
from app.core.config import settings
from app.gateway.llm import LLMClient

CONFIDENCE_THRESHOLD = 0.75
MAX_ATTEMPTS = 2
COLLABORATION_BUDGET_SECONDS = 60.0
AGENT_TIMEOUT_SECONDS = 20.0


class DiagState(TypedDict, total=False):
    submission_id: int
    code: str
    language: str
    verdict: str | None
    evidence: list[dict]
    draft: DiagnosisDraft
    baseline: DiagnosisDraft
    critique: Critique
    attempts: int
    status: str
    route: str
    llm: object
    packet: dict
    analysis: dict
    use_models: bool
    feedback: list[str]
    steps: list[dict]
    deadline: float
    observer: object


def step(state, agent, method, status, summary, *, evidence_ids=None, feedback=None):
    return [*state.get("steps", []), {"agent": agent, "method": method, "status": status,
        "round": state.get("attempts", 0) + 1, "summary": summary[:1800],
        "evidence_ids": evidence_ids or [], "feedback": feedback or []}]


async def bounded_call(state, role, operation):
    remaining = state["deadline"] - time.monotonic()
    if remaining <= 0:
        return None, "budget_exhausted"
    client = state.get("llm")
    if hasattr(client, "last_call"):
        client.last_call = None
    reason = ""
    try:
        value = await asyncio.wait_for(operation(), min(remaining, AGENT_TIMEOUT_SECONDS))
        if value is None:
            reason = getattr(getattr(client, "last_call", None), "error", "") or "invalid_agent_output"
    except TimeoutError:
        value, reason = None, "agent_timeout"
    except Exception:
        value, reason = None, "invalid_agent_output"
    observer = state.get("observer")
    if observer is not None:
        await observer(role, value is not None, reason)
    return value, reason


async def evidence_node(state: DiagState) -> dict:
    baseline = diagnose(state.get("evidence", []), state.get("verdict"), state.get("code", ""),
                        language=state.get("language", "cpp17"))
    packet = evidence_packet(state.get("evidence", []), state.get("code", ""),
                             state.get("language", "cpp17"), state.get("verdict"))
    deterministic = any(n.p in ("P0", "P1") for n in baseline.nature)
    eligible = state.get("verdict") in ("WA", "RE", "TLE", "MLE", "CE") and not deterministic
    use_models = eligible and bool(packet["evidence"]) and bool(getattr(state.get("llm"), "available", False))
    result = {"baseline": baseline, "packet": packet, "use_models": use_models, "analysis": {}}
    ids = [e["id"] for e in packet["evidence"]]
    if not use_models:
        reason = ("编译或字符错误已有确定性证据，采用规则快速路径。" if deterministic else
                  "本次没有需要扩展分析的失败结果。" if not eligible else
                  "外部模型不可用或没有可用执行证据，仅保留规则分析。")
        return {**result, "steps": step(state, "evidence", "rules", "completed", reason, evidence_ids=ids)}
    value, reason = await bounded_call(state, "evidence", lambda: analyze_evidence(state["llm"], packet))
    if value is None:
        return {**result, "steps": step(state, "evidence", "model", "unavailable",
            "未取得可用的证据分析，后续直接核对原始记录。", feedback=[reason], evidence_ids=ids)}
    return {**result, "analysis": value.model_dump(), "steps": step(state, "evidence", "model", "completed",
        "\n".join(o.summary for o in value.observations),
        evidence_ids=sorted({i for o in value.observations for i in o.evidence_ids}))}


async def diagnose_node(state: DiagState) -> dict:
    baseline = state["baseline"]
    if not state["use_models"]:
        return {"draft": baseline, "steps": step(state, "diagnosis", "rules", "completed", baseline.narrative)}
    context = {"original_records": state["packet"], "evidence_agent": state["analysis"]}
    value, reason = await bounded_call(state, "diagnosis", lambda: llm_hypothesis(
        baseline, state.get("evidence", []), state.get("verdict"), state.get("code", ""), state.get("llm"),
        language=state.get("language", "cpp17"), context=context,
        feedback=state.get("feedback"), previous=state.get("draft")))
    if value is None:
        return {"draft": baseline, "steps": step(state, "diagnosis", "model", "unavailable",
            "未取得可核对的候选错因，保留已有执行事实。", feedback=[reason])}
    return {"draft": value, "steps": step(state, "diagnosis", "model", "completed", value.narrative,
        evidence_ids=sorted({i for claim in value.mechanism for i in claim.evidence_ids}))}


async def verify_node(state: DiagState) -> dict:
    ids = {e["id"] for e in state.get("evidence", []) if type(e.get("id")) is int}
    critique = review(state["draft"], ids)
    result = {"critique": critique, "attempts": state.get("attempts", 0) + 1, "feedback": []}
    if not critique.passed:
        return {**result, "feedback": critique.issues if state["use_models"] else [],
                "steps": step(state, "critic", "rules", "revise", "候选解释未通过证据约束检查。", feedback=critique.issues)}
    if not state["use_models"] or "llm_hypothesis" not in state["draft"].degraded:
        return {**result, "steps": step(state, "critic", "rules", "completed",
            "已检查引用和结构；本次没有可供独立 AI 审查的候选错因。")}
    value, reason = await bounded_call(state, "critic", lambda: review_diagnosis(state["llm"], state["packet"], state["draft"]))
    if value is None:
        return {**result, "critique": Critique(passed=False, issues=["独立审查不可用"], severity="block"),
                "steps": step(state, "critic", "model", "unavailable",
                    "独立审查未完成，不将模型假设作为协作结论展示。", feedback=[reason])}
    passed = value.decision == "approve"
    return {**result, "critique": Critique(passed=passed, issues=value.issues,
                                          severity="info" if passed else "block"),
        "feedback": value.issues if value.decision == "revise" else [],
        "steps": step(state, "critic", "model", value.decision, value.summary,
                      evidence_ids=value.evidence_ids, feedback=value.issues)}


def after_verify(state: DiagState) -> str:
    if not state["critique"].passed and state.get("feedback") and state.get("attempts", 0) < MAX_ATTEMPTS:
        return "diagnose"
    return "gate"


def gate_node(state: DiagState) -> dict:
    d = state["draft"]
    critique = state.get("critique")
    if critique is None or not critique.passed:
        safe = d.model_copy(update={"mechanism": [], "cognition": [], "nature": [],
            "narrative": "诊断未通过独立审查或证据校验，请对照实际编译和运行记录，等待教练复核。",
            "confidence": 0.0, "need_human": True, "degraded": [*d.degraded, "critic_blocked"]})
        return {"status": "needs_human", "route": "human", "draft": safe}
    if state.get("verdict") in ("SAMPLE_PASS", "NO_TESTS") and not d.nature:
        return {"status": "inconclusive", "route": "verification_needed"}
    if state.get("verdict") == "AC" and not d.nature:
        return {"status": "confirmed", "route": "none"}
    if d.need_human or d.confidence < CONFIDENCE_THRESHOLD:
        return {"status": "needs_human", "route": "llm_hypothesis" if "llm_hypothesis" in d.degraded else "human"}
    route = "fast_fix" if any(n.p == "P0" for n in d.nature) else "syntax_help" if any(n.p == "P1" for n in d.nature) else "full_pipeline"
    return {"status": "confirmed", "route": route}


def build_graph():
    g = StateGraph(DiagState)
    g.add_node("evidence", evidence_node)
    g.add_node("diagnose", diagnose_node)
    g.add_node("verify", verify_node)
    g.add_node("gate", gate_node)
    g.set_entry_point("evidence")
    g.add_edge("evidence", "diagnose")
    g.add_edge("diagnose", "verify")
    g.add_conditional_edges("verify", after_verify, {"diagnose": "diagnose", "gate": "gate"})
    g.add_edge("gate", END)
    return g.compile()


GRAPH = build_graph()


async def run_diagnosis(*, submission_id: int, code: str, verdict: str | None,
                        evidence: list[dict], language: str = "cpp17", llm=..., observer=None) -> dict:
    out = await GRAPH.ainvoke({"submission_id": submission_id, "code": code, "language": language,
        "verdict": verdict, "evidence": evidence, "attempts": 0, "steps": [], "feedback": [],
        "deadline": time.monotonic() + COLLABORATION_BUDGET_SECONDS, "observer": observer,
        "llm": (LLMClient() if settings.llm_enabled else None) if llm is ... else llm})
    steps = out.get("steps", [])
    modeled = {s["agent"] for s in steps if s["method"] == "model" and s["status"] in ("completed", "approve")}
    mode = "multi_agent" if modeled == {"evidence", "diagnosis", "critic"} and out["critique"].passed else "degraded" if out.get("use_models") else "rules"
    collaboration = {"version": "collaboration-v1", "mode": mode, "steps": steps,
                     "rounds": out.get("attempts", 0), "human_review_required": out.get("status") == "needs_human"}
    return {"status": out.get("status", "needs_human"), "route": out.get("route", "human"),
            "attempts": out.get("attempts", 0), "draft": out["draft"],
            "critique": out.get("critique"), "collaboration": collaboration}
