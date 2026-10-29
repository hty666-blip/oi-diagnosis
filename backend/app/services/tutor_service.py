"""答疑会话服务：把导师策略落到数据库。"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.compose import compose
from app.agents.tutor import (
    DiagnosisView,
    SessionView,
    decide,
    is_confused,
    safe_output,
)
from app.core.config import settings
from app.domain.judging import outputs_equal
from app.domain.models import Diagnosis, Evidence, HintSession, HintTurn, Submission, User
from app.gateway.llm import LLMClient
from app.services.access import accessible_hint_session, accessible_submission
from app.services.judgement import effective_verdict


class TutorService:
    def __init__(self, session: AsyncSession, user: User) -> None:
        self.session = session
        self.user = user

    async def create(self, submission_id: int) -> tuple[HintSession, HintTurn]:
        sub = await accessible_submission(self.session, self.user, submission_id, lock=True)
        if sub.stage != "READY" or sub.verdict is None:
            raise HTTPException(status_code=409, detail="请先完成该提交的判题")

        diag = (
            await self.session.execute(
                select(Diagnosis).where(Diagnosis.submission_id == submission_id)
            )
        ).scalar_one_or_none()
        if diag is None:
            raise HTTPException(status_code=409, detail="请先对该提交执行诊断")

        # 同一提交的重复点击或前端重复挂载复用会话；提交行锁避免并发创建。
        existing = (
            await self.session.execute(
                select(HintSession).where(
                    HintSession.submission_id == submission_id,
                    HintSession.student_id == sub.student_id,
                    HintSession.status == "open",
                ).order_by(HintSession.id.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if existing:
            last_turn = (
                await self.session.execute(
                    select(HintTurn).where(
                        HintTurn.session_id == existing.id, HintTurn.role == "tutor"
                    ).order_by(HintTurn.id.desc()).limit(1)
                )
            ).scalar_one_or_none()
            if last_turn:
                return existing, last_turn

        hs = HintSession(submission_id=submission_id, student_id=sub.student_id, level=0, status="open")
        self.session.add(hs)
        await self.session.flush()

        view = await self._diagnosis_view(diag, submission_id)
        decision = decide(SessionView(level=0, has_student_input=False), "", view)
        turn = HintTurn(session_id=hs.id, role="tutor", level=decision.level,
                        content=decision.content)
        self.session.add(turn)
        await self.session.commit()
        await self.session.refresh(hs)
        await self.session.refresh(turn)
        return hs, turn

    async def reply(self, session_id: int, message: str) -> dict:
        hs = await accessible_hint_session(self.session, self.user, session_id, lock=True)
        if hs.status in ("resolved", "escalated", "blocked"):
            return {"session_id": hs.id, "status": hs.status, "level": hs.level,
                    "reply": "这次会话已经结束，可以开一个新的。", "action": "closed"}

        source_submission_id = hs.verified_submission_id or hs.submission_id
        diag = (
            await self.session.execute(
                select(Diagnosis).where(Diagnosis.submission_id == source_submission_id)
            )
        ).scalar_one_or_none()
        view = await self._diagnosis_view(diag, source_submission_id)

        student_turns = (
            await self.session.execute(
                select(func.count()).select_from(HintTurn)
                .where(HintTurn.session_id == hs.id, HintTurn.role == "student")
            )
        ).scalar() or 0

        recent = (
            await self.session.execute(
                select(HintTurn).where(HintTurn.session_id == hs.id)
                .order_by(HintTurn.id.desc()).limit(12)
            )
        ).scalars().all()

        # 当前层级里，学生已经连续说了几次"不会"
        stuck = 0
        for turn in recent:
            if turn.role != "student":
                continue
            if turn.level != hs.level:
                break
            if is_confused(turn.content):
                stuck += 1
            else:
                break

        decision = decide(
            SessionView(level=hs.level, failed_attempts=hs.failed_attempts,
                        cheat_flags=hs.cheat_flags, status=hs.status,
                        has_student_input=bool(message.strip()), turn=student_turns,
                        stuck_count=stuck),
            message, view,
        )

        # 规则定边界，模型写措辞；模型不可用则用模板
        if decision.action in ("hint", "explain") and decision.mode != "gate":
            llm = LLMClient() if settings.llm_enabled else None
            content = await compose(decision, view, message, llm)
        else:
            content = decision.content

        # 防代做最后一道闸门
        if not safe_output(content):
            content = "这段我先不贴完整代码，按上面的提示改一处，我们再一起看结果。"

        self.session.add(HintTurn(session_id=hs.id, role="student", level=hs.level,
                                  content=message[:4000]))
        self.session.add(HintTurn(session_id=hs.id, role="tutor", level=decision.level,
                                  content=content))
        hs.level = decision.level
        if decision.action == "escalate":
            hs.status = "escalated"
        if decision.action == "refuse":
            hs.cheat_flags += 1
            if hs.cheat_flags >= 3:
                hs.status = "blocked"
        await self.session.commit()

        return {
            "session_id": hs.id,
            "status": hs.status,
            "level": hs.level,
            "action": decision.action,
            "reply": content,
            "dependency_signal": decision.dependency_signal,
            "mastery": None,
        }

    async def verify(self, session_id: int, submission_id: int) -> dict:
        """绑定修改后真实提交；样例通过和学生口述均不能关闭答疑。"""
        hs = await accessible_hint_session(self.session, self.user, session_id, lock=True)
        original = await accessible_submission(self.session, self.user, hs.submission_id)
        candidate = await accessible_submission(self.session, self.user, submission_id)
        if candidate.student_id != hs.student_id or candidate.problem_id != original.problem_id:
            raise HTTPException(status_code=409, detail="核验提交必须属于同一学生和同一道题")
        if candidate.id <= original.id or candidate.code_hash == original.code_hash:
            raise HTTPException(status_code=409, detail="请先修改代码并重新提交，再核验新提交")
        if candidate.stage != "READY" or candidate.verdict is None:
            raise HTTPException(status_code=409, detail="新提交尚未完成判题")
        if hs.status == "resolved":
            if hs.verified_submission_id != candidate.id:
                raise HTTPException(status_code=409, detail="答疑已经绑定其他通过题包核验的提交")
            return self._verification_reply(hs, candidate, "这次答疑已完成核验。")
        if hs.status in ("escalated", "blocked"):
            raise HTTPException(status_code=409, detail="这次答疑已结束，请新建答疑后再核验")

        if hs.verified_submission_id == candidate.id:
            return self._verification_reply(hs, candidate)
        if hs.verified_submission_id and candidate.id < hs.verified_submission_id:
            raise HTTPException(status_code=409, detail="请核验更新的提交，不能回退到之前的版本")

        hs.verified_submission_id = candidate.id
        counts = candidate.tests_passed or {}
        complete = (
            effective_verdict(candidate) == "AC"
            and counts.get("scope") == "package"
            and isinstance(counts.get("total"), int)
            and counts["total"] > 0
            and counts.get("passed") == counts["total"]
        )
        if complete:
            hs.status = "resolved"
        elif effective_verdict(candidate) not in ("SAMPLE_PASS", "NO_TESTS"):
            hs.failed_attempts += 1

        result = self._verification_reply(hs, candidate)
        self.session.add(HintTurn(
            session_id=hs.id, role="tutor", level=hs.level,
            content=f"核验提交 #{candidate.id}：{result['reply']}",
        ))
        # 这里不更新 BKT：一次执行或聊天不能证明知识点掌握程度。
        await self.session.commit()
        return result

    @staticmethod
    def _verification_reply(hs: HintSession, candidate: Submission, text: str = "") -> dict:
        if not text:
            if hs.status == "resolved":
                text = "修改后的代码已通过当前题包测试，这次答疑已解决。"
            elif effective_verdict(candidate) == "SAMPLE_PASS":
                text = "修改后的代码仅通过公开样例；答疑保留，仍需检查边界数据。"
            elif effective_verdict(candidate) == "NO_TESTS":
                text = "目前没有可执行测试，无法核验修改是否正确；请补充测试或请教师复核。"
            else:
                text = "修改后的代码仍有失败测试，请对照这次提交的证据继续检查。"
        return {
            "session_id": hs.id, "status": hs.status, "level": hs.level,
            "action": "resolved" if hs.status == "resolved" else "verify",
            "reply": text, "mastery": None,
            "verified_submission_id": hs.verified_submission_id,
            "verdict": effective_verdict(candidate),
        }

    async def _diagnosis_view(self, diag: Diagnosis | None, submission_id: int) -> DiagnosisView:
        view = _diag_view(diag)
        rows = (
            await self.session.execute(
                select(Evidence).where(
                    Evidence.submission_id == submission_id, Evidence.type == "run_trace"
                ).order_by(Evidence.id)
            )
        ).scalars().all()
        for evidence in rows:
            payload = evidence.payload or {}
            failed = (
                payload.get("matched") is False
                or payload.get("status") != "Accepted"
                or payload.get("exit_code", 0) != 0
                or (payload.get("matched") is not True and not outputs_equal(
                    str(payload.get("stdout", "")), str(payload.get("expected", ""))
                ))
            )
            if failed:
                if not payload.get("input_truncated"):
                    view.counterexample = str(payload.get("stdin", payload.get("input", "")))
                view.counterexample_minimized = False
                break
        return view

    async def get(self, session_id: int) -> dict:
        hs = await accessible_hint_session(self.session, self.user, session_id)
        turns = (
            await self.session.execute(
                select(HintTurn).where(HintTurn.session_id == hs.id).order_by(HintTurn.id)
            )
        ).scalars().all()
        return {
            "session_id": hs.id,
            "status": hs.status,
            "level": hs.level,
            "cheat_flags": hs.cheat_flags,
            "submission_id": hs.submission_id,
            "verified_submission_id": hs.verified_submission_id,
            "turns": [{"role": t.role, "level": t.level, "content": t.content} for t in turns],
        }


def _diag_view(diag: Diagnosis | None) -> DiagnosisView:
    if diag is None:
        return DiagnosisView()
    nature = diag.nature or []
    top = nature[0] if nature else {}
    mechanism = (diag.mechanism or [{}])[0]
    return DiagnosisView(
        p=str(top.get("p", "P2")),
        code=str(top.get("code", mechanism.get("code", ""))),
        message=str(top.get("message", mechanism.get("explain", diag.narrative or ""))),
        line=top.get("line"),
        col=top.get("col"),
        hint=str(top.get("hint", (diag.model_meta or {}).get("hint_seed", ""))),
        evidence_summary=diag.narrative or "",
        hypothesis="llm_hypothesis" in (diag.degraded or [])
                   and (diag.reviewer_id is None or diag.status != "confirmed"),
    )


