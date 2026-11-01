from sqlalchemy import select

from app.agents.diagnostician import failure_summary
from app.domain.models import Diagnosis, Evidence
from app.services.tutor_service import TutorService, _diag_view


async def test_normal_student_thought_can_be_answered_without_a_model(api_context):
    ctx = api_context
    submission = await ctx.submission()
    opened = await ctx.client.post("/api/v1/sessions", headers=ctx.headers(),
                                   json={"submission_id": submission.id})
    assert opened.status_code == 200, opened.text
    reply = await ctx.client.post(
        f"/api/v1/sessions/{opened.json()['session_id']}/messages", headers=ctx.headers(),
        json={"content": "我用循环把所有数加起来，觉得是累加结果超过了 int 范围"},
    )
    assert reply.status_code == 200, reply.text
    assert reply.json()["status"] == "open"
    assert reply.json()["action"] != "verify"
    assert reply.json()["reply"].strip()


async def test_truncated_matching_prefixes_do_not_hide_a_real_failed_case(api_context):
    ctx = api_context
    submission = await ctx.submission()
    payload = {"status": "Accepted", "matched": False, "input": "1\n",
               "stdout": "x" * 4000, "expected": "x" * 4000,
               "stdout_truncated": True, "expected_truncated": True}
    ctx.session.add(Evidence(submission_id=submission.id, type="run_trace", payload=payload))
    await ctx.session.flush()
    view = await TutorService(ctx.session, ctx.users.student)._diagnosis_view(None, submission.id)
    assert view.counterexample == "1\n"
    assert view.counterexample_minimized is False
    summary = failure_summary([{"id": 1, "type": "run_trace", "payload": payload}])
    assert "截断" in summary
    assert "输出不一致" in summary


async def test_teacher_confirmed_explanation_is_no_longer_a_machine_hypothesis(api_context):
    ctx = api_context
    submission = await ctx.submission()
    diag = await ctx.session.scalar(select(Diagnosis).where(Diagnosis.submission_id == submission.id))
    diag.degraded = ["llm_hypothesis"]
    assert _diag_view(diag).hypothesis is True
    diag.reviewer_id = ctx.users.teacher.id
    diag.status = "confirmed"
    assert _diag_view(diag).hypothesis is False
    diag.status = "needs_human"
    assert _diag_view(diag).hypothesis is True


async def test_model_probe_respects_disabled_setting_and_teacher_role(api_context, monkeypatch):
    ctx = api_context

    async def forbidden(*args, **kwargs):
        raise AssertionError("禁用模型时不得产生外部调用")

    monkeypatch.setattr("app.gateway.llm.LLMClient.ping", forbidden)
    state = await ctx.client.get("/api/v1/system/llm")
    assert state.json()["enabled"] is False
    assert state.json()["available"] is False
    assert (await ctx.client.post("/api/v1/system/llm/ping", headers=ctx.headers())).status_code == 403
    disabled = await ctx.client.post("/api/v1/system/llm/ping", headers=ctx.headers(ctx.users.teacher))
    assert disabled.status_code == 409


async def test_legacy_sample_success_copy_is_corrected_without_changing_original_diagnosis(api_context):
    ctx = api_context
    submission = await ctx.submission(verdict="AC", tests_passed={"passed": 1, "total": 1})
    diag = await ctx.session.scalar(select(Diagnosis).where(Diagnosis.submission_id == submission.id))
    diag.narrative = "样例全部通过，暂无问题。"
    await ctx.session.commit()
    history = await ctx.client.get("/api/v1/me/submissions", headers=ctx.headers())
    assert history.json()[0]["verdict"] == "SAMPLE_PASS"
    assert "暂无问题" not in history.json()[0]["narrative"]
    assert "尚未验证" in history.json()[0]["narrative"]
    detail = await ctx.client.get(f"/api/v1/submissions/{submission.id}", headers=ctx.headers())
    assert "暂无问题" not in detail.json()["diagnosis"]["narrative"]
    card = await ctx.client.get(f"/api/v1/submissions/{submission.id}/card", headers=ctx.headers())
    assert "暂无问题" not in card.text
    await ctx.session.refresh(diag)
    assert diag.narrative == "样例全部通过，暂无问题。"
