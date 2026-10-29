import pytest

from app.agents.contracts import CauseClaim, Critique, DiagnosisDraft, NatureTag
from app.agents.critic import review
from app.orchestrator.graph import CONFIDENCE_THRESHOLD, gate_node, run_diagnosis


def _evidence_static(findings):
    return [{"id": 1, "type": "static_alert", "payload": {"findings": findings}}]


def _p0_finding():
    return {"p": "P0", "code": "P0-FULLWIDTH", "line": 2, "col": 20,
            "message": "中文/全角符号「；」", "hint": "改成英文半角「;」",
            "autofix": ";", "confidence": 1.0}


def test_claim_without_evidence_is_rejected():
    with pytest.raises(ValueError, match="没有绑定证据"):
        CauseClaim(code="M01", confidence=0.9)


def test_draft_requires_narrative():
    with pytest.raises(ValueError, match="narrative"):
        DiagnosisDraft(narrative="  ")


def test_critic_blocks_unknown_evidence():
    d = DiagnosisDraft(
        mechanism=[CauseClaim(code="M01", confidence=0.8, evidence_ids=[99])],
        nature=[NatureTag(p="P1", code="P1-MISSING-SEMICOLON")],
        narrative="缺少分号",
        confidence=0.8,
    )
    c = review(d, evidence_ids={1})
    assert c.passed is False
    assert c.severity == "block"


def test_critic_blocks_full_program_output():
    d = DiagnosisDraft(
        nature=[NatureTag(p="P1", code="X")],
        narrative="改法如下：\n```cpp\nint main(){return 0;}\n```",
        confidence=0.8,
    )
    c = review(d, evidence_ids=set())
    assert any("防代做" in i for i in c.issues)
    assert c.passed is False


async def test_graph_confirms_p0_and_routes_fast_fix():
    out = await run_diagnosis(submission_id=1, code="", verdict="CE",
                              evidence=_evidence_static([_p0_finding()]))
    assert out["status"] == "confirmed"
    assert out["route"] == "fast_fix"
    assert out["draft"].first_hint_level == 0
    assert out["draft"].hint_seed


async def test_graph_routes_syntax_help_for_compile_error():
    ev = [{
        "id": 2, "type": "compile_log",
        "payload": {"stderr": "main.cpp:4:5: error: expected ';' before 'printf'\n"},
    }]
    out = await run_diagnosis(submission_id=2, code="", verdict="CE", evidence=ev)
    assert out["status"] == "confirmed"
    assert out["route"] == "syntax_help"


async def test_graph_ac_is_confirmed_none():
    out = await run_diagnosis(submission_id=9, code="", verdict="AC", evidence=[])
    assert out["status"] == "confirmed"
    assert out["route"] == "none"


async def test_graph_sends_unknown_failure_to_human():
    out = await run_diagnosis(submission_id=3, code="int main(){}", verdict="WA", evidence=[])
    assert out["status"] == "needs_human"
    assert out["route"] == "human"
    assert out["draft"].confidence < CONFIDENCE_THRESHOLD


async def test_graph_is_deterministic():
    ev = _evidence_static([_p0_finding()])
    a = await run_diagnosis(submission_id=1, code="", verdict="CE", evidence=ev)
    b = await run_diagnosis(submission_id=1, code="", verdict="CE", evidence=ev)
    assert a["draft"].narrative == b["draft"].narrative
    assert a["route"] == b["route"]


def test_rejected_critique_blocks_even_high_confidence_and_removes_unsafe_content():
    draft = DiagnosisDraft(narrative="```cpp\nint main(){}\n```", confidence=1,
                           nature=[NatureTag(p="P0", code="P0-X")])
    out = gate_node({"verdict": "AC", "draft": draft,
                     "critique": Critique(passed=False, issues=["防代做"], severity="block")})
    assert out["status"] == "needs_human"
    assert out["route"] == "human"
    assert "main" not in out["draft"].narrative
    assert out["draft"].confidence == 0


@pytest.mark.parametrize("verdict", ["SAMPLE_PASS", "NO_TESTS"])
async def test_unverified_result_never_confirms_or_invents_an_error(verdict, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("未验证的成功状态不应触发模型诊断")

    monkeypatch.setattr("app.orchestrator.graph.llm_hypothesis", forbidden)
    out = await run_diagnosis(submission_id=1, code="int main(){}", verdict=verdict, evidence=[])
    assert out["status"] == "inconclusive"
    assert out["route"] == "verification_needed"
    assert not out["draft"].mechanism
