"""错题卡接口：把一次诊断导出成 Markdown。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.domain.models import Diagnosis, Problem
from app.infra.db import get_session
from app.services.access import accessible_submission
from app.services.judgement import effective_verdict
from app.services.wrong_card import CardInput, render_card

router = APIRouter(prefix="/submissions", tags=["card"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/{submission_id}/card", response_class=PlainTextResponse)
async def get_card(submission_id: int, session: SessionDep, user: CurrentUser) -> str:
    sub = await accessible_submission(session, user, submission_id)
    problem = await session.get(Problem, sub.problem_id)
    diag = (
        await session.execute(
            select(Diagnosis).where(Diagnosis.submission_id == submission_id)
        )
    ).scalar_one_or_none()

    verdict = effective_verdict(sub)

    next_action = ""
    if diag and diag.status == "needs_human":
        next_action = "已进教练复核队列，先按提示自己试一次。"
    elif verdict == "AC":
        next_action = "已通过当前题包测试，可继续练习同知识点题目。"
    elif verdict == "SAMPLE_PASS":
        next_action = "仅通过公开样例，仍需检查边界数据，不能据此认定整题通过。"
    elif verdict == "NO_TESTS":
        next_action = "没有可执行测试，请补充合法测试数据或请教师复核。"

    return render_card(CardInput(
        pid=problem.pid if problem else str(sub.problem_id),
        title=problem.title if problem else "",
        verdict=verdict or "",
        narrative=diag.narrative if diag else "",
        nature=(
            diag.nature
            if diag and diag.nature
            else [
                {"p": "推断", "code": m.get("code", ""), "message": m.get("explain", "")}
                for m in (diag.mechanism or [])
            ]
            if diag
            else []
        ),
        code_lines=(sub.code or "").splitlines(),
        mastery=None,
        next_action=next_action,
    ))

