"""诊断流程的状态图（LangGraph）。

    diagnose ──▶ verify ──(打回, ≤2 次)──▶ diagnose
                   │
                   └──▶ gate ──▶ confirmed / needs_human

设计要点：
- 图是纯函数式的：证据由外部注入，节点不碰数据库 → 好测、好回放
- 打回只重跑诊断，不重跑判题（判题是一次性的、有成本的）
- gate 的阈值（0.75）与需求文档 §4.1 一致
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, StateGraph

from app.agents.contracts import Critique, DiagnosisDraft
from app.agents.critic import review
from app.agents.diagnostician import diagnose, llm_hypothesis
from app.core.config import settings
from app.gateway.llm import LLMClient

CONFIDENCE_THRESHOLD = 0.75
MAX_ATTEMPTS = 2


class DiagState(TypedDict, total=False):
    submission_id: int
    code: str
    verdict: str | None
    evidence: list[dict]
    draft: DiagnosisDraft
    critique: Critique
    attempts: int
    status: str
    route: str
    llm: object


async def diagnose_node(state: DiagState) -> dict:
    draft = diagnose(state.get("evidence", []), state.get("verdict"), state.get("code", ""))
    # 规则判不了（need_human）才动用模型；能确定性判的绝不问模型
    if draft.need_human and state.get("verdict") not in ("SAMPLE_PASS", "NO_TESTS"):
        better = await llm_hypothesis(
            draft, state.get("evidence", []), state.get("verdict"),
            state.get("code", ""), state.get("llm"),
        )
        if better is not None:
            draft = better
    return {"draft": draft}


def verify_node(state: DiagState) -> dict:
    ids = {e["id"] for e in state.get("evidence", []) if "id" in e}
    critique = review(state["draft"], ids)
    return {"critique": critique, "attempts": state.get("attempts", 0) + 1}


def after_verify(state: DiagState) -> str:
    if not state["critique"].passed and state.get("attempts", 0) < MAX_ATTEMPTS:
        return "diagnose"
    return "gate"


def gate_node(state: DiagState) -> dict:
    d = state["draft"]
    critique = state.get("critique")
    if critique is None or not critique.passed:
        safe = d.model_copy(update={
            "mechanism": [], "cognition": [], "nature": [],
            "narrative": "诊断未通过证据校验，请先查看实际编译或运行记录，等待教练复核。",
            "confidence": 0.0, "need_human": True,
            "degraded": [*d.degraded, "critic_blocked"],
        })
        return {"status": "needs_human", "route": "human", "draft": safe}
    if state.get("verdict") in ("SAMPLE_PASS", "NO_TESTS") and not d.nature:
        return {"status": "inconclusive", "route": "verification_needed"}
    # 全部通过：没有需要诊断的东西，直接放行（别把它送进人工队列）
    if state.get("verdict") == "AC" and not d.nature:
        return {"status": "confirmed", "route": "none"}
    if d.need_human or d.confidence < CONFIDENCE_THRESHOLD:
        # 模型只提假设，不能直接确认；单独打标，教练队列可优先看
        if "llm_hypothesis" in (d.degraded or []):
            return {"status": "needs_human", "route": "llm_hypothesis"}
        return {"status": "needs_human", "route": "human"}

    if any(n.p == "P0" for n in d.nature):
        route = "fast_fix"
    elif any(n.p == "P1" for n in d.nature):
        route = "syntax_help"
    else:
        route = "full_pipeline"
    return {"status": "confirmed", "route": route}


def build_graph():
    g = StateGraph(DiagState)
    g.add_node("diagnose", diagnose_node)
    g.add_node("verify", verify_node)
    g.add_node("gate", gate_node)

    g.set_entry_point("diagnose")
    g.add_edge("diagnose", "verify")
    g.add_conditional_edges("verify", after_verify, {"diagnose": "diagnose", "gate": "gate"})
    g.add_edge("gate", END)
    return g.compile()


GRAPH = build_graph()


async def run_diagnosis(*, submission_id: int, code: str, verdict: str | None,
                        evidence: list[dict]) -> dict:
    out = await GRAPH.ainvoke({
        "submission_id": submission_id,
        "code": code,
        "verdict": verdict,
        "evidence": evidence,
        "attempts": 0,
        "llm": LLMClient() if settings.llm_enabled else None,
    })
    return {
        "status": out.get("status", "needs_human"),
        "route": out.get("route", "human"),
        "attempts": out.get("attempts", 0),
        "draft": out["draft"],
        "critique": out.get("critique"),
    }




