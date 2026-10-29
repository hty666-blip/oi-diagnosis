"""提交相关接口（M1：同步执行，M4 改为入队 + SSE）。"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.capabilities.sandbox import SandboxError
from app.domain.models import Diagnosis, Evidence, JudgeJob, Problem, Submission, User
from app.infra.db import get_session
from app.orchestrator.graph import run_diagnosis
from app.services.access import accessible_submission
from app.services.job_service import JobService, job_out
from app.services.judgement import effective_verdict, result_narrative
from app.services.submission_service import SubmissionService

router = APIRouter(prefix="/submissions", tags=["submissions"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
logger = logging.getLogger(__name__)


class SubmissionCreate(BaseModel):
    problem_pid: str = Field(..., min_length=1, max_length=32, examples=["P1001"])
    code: str = Field(..., min_length=1, max_length=100_000)
    student_id: int | None = None
    parent_submission_id: int | None = Field(default=None, gt=0)

    @field_validator("code", "problem_pid", mode="before")
    @classmethod
    def reject_unstorable_text(cls, value):
        if not isinstance(value, str) or "\x00" in value:
            raise HTTPException(status_code=422, detail="题号和代码必须是有效文本，不能包含空字符")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise HTTPException(status_code=422, detail="题号或代码包含无法保存的字符") from exc
        return value

    @field_validator("code")
    @classmethod
    def reject_blank_code(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请输入要检查的代码")
        return value


class SubmissionOut(BaseModel):
    id: int
    code: str
    problem_pid: str
    problem_title: str
    parent_submission_id: int | None = None
    stage: str
    verdict: str | None
    time_ms: int | None
    memory_kb: int | None
    tests_passed: dict | None
    diagnosis: dict | None = None
    job: dict | None = None


def _to_out(
    sub: Submission, diag: Diagnosis | None = None, problem: Problem | None = None,
    job: JudgeJob | None = None,
    user: User | None = None,
) -> SubmissionOut:
    return SubmissionOut(
        id=sub.id,
        code=sub.code,
        problem_pid=problem.pid if problem else "",
        problem_title=problem.title if problem else "",
        parent_submission_id=sub.parent_submission_id,
        stage=sub.stage,
        verdict=effective_verdict(sub),
        time_ms=sub.time_ms,
        memory_kb=sub.memory_kb,
        tests_passed=sub.tests_passed,
        job=job_out(job, can_cancel=user is not None and (user.role == "admin" or user.id == sub.student_id)),
        diagnosis=(
            {
                "mechanism": diag.mechanism,
                "nature": diag.nature,
                "narrative": result_narrative(sub, diag.narrative),
                "status": diag.status,
                "route": (diag.model_meta or {}).get("route"),
                "autofixable": (diag.model_meta or {}).get("autofixable", False),
                "degraded": diag.degraded or [],
                "confidence": diag.confidence,
                "review_revision": diag.review_revision,
                "reviewer_id": diag.reviewer_id,
                "reviewed_at": diag.reviewed_at.isoformat() if diag.reviewed_at else None,
            }
            if diag
            else None
        ),
    )


@router.post("", response_model=SubmissionOut)
async def create_submission(
    payload: SubmissionCreate,
    session: SessionDep,
    user: CurrentUser,
    response: Response,
    prefer: Annotated[str | None, Header()] = None,
) -> SubmissionOut:
    if payload.student_id not in (None, user.id):
        raise HTTPException(status_code=403, detail="只能以当前登录账号提交代码")
    svc = SubmissionService(session)
    background = "respond-async" in [part.strip().lower() for part in (prefer or "").split(",")]
    try:
        sub = await svc.create(payload.problem_pid, payload.code, user.id, payload.parent_submission_id,
                               background=background)
    except SandboxError as e:
        logger.warning("提交尚未进入判题：%s", type(e).__name__)
        raise HTTPException(status_code=503, detail="题目或执行服务暂时不可用，请稍后重试") from e
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    if sub.stage == "READY":
        diag = (
            await session.execute(select(Diagnosis).where(Diagnosis.submission_id == sub.id))
        ).scalar_one_or_none()
        job = (await session.execute(select(JudgeJob).where(JudgeJob.submission_id == sub.id))).scalar_one_or_none()
        return _to_out(sub, diag, await session.get(Problem, sub.problem_id), job, user)

    if background:
        job = (await session.execute(select(JudgeJob).where(JudgeJob.submission_id == sub.id))).scalar_one_or_none()
        if job is None:
            raise HTTPException(status_code=409, detail="该提交正在同步执行，请稍后查询结果",
                                headers={"Location": f"/api/v1/submissions/{sub.id}"})
        response.status_code = 202
        response.headers["Location"] = f"/api/v1/submissions/{sub.id}"
        response.headers["Retry-After"] = "1"
        return _to_out(sub, None, await session.get(Problem, sub.problem_id), job, user)

    if not svc.was_created:
        raise HTTPException(status_code=409, detail=f"提交 #{sub.id} 正在判题，请稍后查看结果",
                            headers={"Location": f"/api/v1/submissions/{sub.id}"})

    try:
        await svc.run(sub)
    except SandboxError as e:
        logger.warning("判题执行暂时失败：%s", type(e).__name__)
        raise HTTPException(status_code=503, detail="判题服务暂时不可用，请稍后重试") from e

    diag = (
        await session.execute(select(Diagnosis).where(Diagnosis.submission_id == sub.id))
    ).scalar_one_or_none()
    job = (await session.execute(select(JudgeJob).where(JudgeJob.submission_id == sub.id))).scalar_one_or_none()
    return _to_out(sub, diag, await session.get(Problem, sub.problem_id), job, user)


@router.post("/{submission_id}/retry", response_model=SubmissionOut, status_code=202)
async def retry_submission(
    submission_id: int, session: SessionDep, user: CurrentUser, response: Response,
) -> SubmissionOut:
    job = await JobService(session).retry(submission_id, user)
    sub = await accessible_submission(session, user, submission_id)
    response.headers["Location"] = f"/api/v1/submissions/{sub.id}"
    response.headers["Retry-After"] = "1"
    return _to_out(sub, None, await session.get(Problem, sub.problem_id), job, user)


@router.post("/{submission_id}/cancel", response_model=SubmissionOut)
async def cancel_submission(
    submission_id: int, session: SessionDep, user: CurrentUser,
) -> SubmissionOut:
    job = await JobService(session).cancel(submission_id, user)
    sub = await accessible_submission(session, user, submission_id)
    diag = (await session.execute(select(Diagnosis).where(Diagnosis.submission_id == sub.id))).scalar_one_or_none()
    return _to_out(sub, diag, await session.get(Problem, sub.problem_id), job, user)


@router.get("/{submission_id}", response_model=SubmissionOut)
async def get_submission(
    submission_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> SubmissionOut:
    sub = await accessible_submission(session, user, submission_id)
    diag = (
        await session.execute(select(Diagnosis).where(Diagnosis.submission_id == sub.id))
    ).scalar_one_or_none()
    job = (await session.execute(select(JudgeJob).where(JudgeJob.submission_id == sub.id))).scalar_one_or_none()
    return _to_out(sub, diag, await session.get(Problem, sub.problem_id), job, user)


@router.get("/{submission_id}/evidence")
async def get_evidence(
    submission_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> list[dict]:
    await accessible_submission(session, user, submission_id)
    rows = (
        await session.execute(
            select(Evidence).where(Evidence.submission_id == submission_id).order_by(Evidence.id)
        )
    ).scalars().all()
    return [
        {"id": e.id, "type": e.type, "payload": e.payload, "digest": e.digest,
         "reproducible": e.reproducible}
        for e in rows
    ]

@router.post("/{submission_id}/diagnose")
async def diagnose_submission(
    submission_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> dict:
    """对已有提交跑多 Agent 诊断（证据来自数据库，不重复判题）。"""
    sub = await accessible_submission(session, user, submission_id)
    if sub.stage != "READY" or sub.verdict is None:
        raise HTTPException(status_code=409, detail="提交尚未完成判题，请先等待结果或重新提交")
    before = (
        await session.execute(select(Diagnosis).where(Diagnosis.submission_id == sub.id))
    ).scalar_one_or_none()
    if before and before.reviewer_id is not None:
        raise HTTPException(status_code=409, detail="该诊断已有教师复核，请保留审定记录或提交新版本代码")
    initial_revision = before.review_revision if before else 0

    rows = (
        await session.execute(
            select(Evidence).where(Evidence.submission_id == submission_id).order_by(Evidence.id)
        )
    ).scalars().all()
    evidence = [{"id": e.id, "type": e.type, "payload": e.payload} for e in rows]

    out = await run_diagnosis(
        submission_id=sub.id, code=sub.code, verdict=effective_verdict(sub), evidence=evidence
    )
    draft = out["draft"]

    diag = (
        await session.execute(select(Diagnosis).where(Diagnosis.submission_id == sub.id)
                              .with_for_update().execution_options(populate_existing=True))
    ).scalar_one_or_none() or Diagnosis(submission_id=sub.id)
    if diag.reviewer_id is not None:
        raise HTTPException(status_code=409, detail="该诊断已有教师复核，请保留审定记录或提交新版本代码")
    if (diag.review_revision or 0) != initial_revision:
        raise HTTPException(status_code=409, detail="诊断已更新，请重新读取最新结果")
    diag.mechanism = [c.model_dump() for c in draft.mechanism]
    diag.cognition = [c.model_dump() for c in draft.cognition]
    diag.nature = [n.model_dump() for n in draft.nature]
    diag.narrative = draft.narrative
    diag.confidence = draft.confidence
    diag.status = out["status"]
    diag.degraded = draft.degraded
    diag.model_meta = {
        **(diag.model_meta or {}),
        "route": out["route"],
        "attempts": out["attempts"],
        "first_hint_level": draft.first_hint_level,
        "nature": [n.model_dump() for n in draft.nature],
        "hint_seed": draft.hint_seed,
        "critique_issues": (out["critique"].issues if out.get("critique") else []),
        "engine": "graph-stub-v1",
    }
    diag.review_revision = (diag.review_revision or 0) + 1
    session.add(diag)
    await session.commit()

    return {
        "submission_id": sub.id,
        "verdict": effective_verdict(sub),
        "status": out["status"],
        "route": out["route"],
        "attempts": out["attempts"],
        "confidence": draft.confidence,
        "narrative": draft.narrative,
        "first_hint_level": draft.first_hint_level,
        "nature": [n.model_dump() for n in draft.nature],
        "mechanism": [c.model_dump() for c in draft.mechanism],
        "review_revision": diag.review_revision,
        "reviewer_id": diag.reviewer_id,
        "reviewed_at": diag.reviewed_at.isoformat() if diag.reviewed_at else None,
    }


@router.get("/{submission_id}/lineage")
async def get_lineage(submission_id: int, session: SessionDep, user: CurrentUser) -> list[dict]:
    """返回当前提交的祖先链，不混入同题的其他练习分支。"""
    current = await accessible_submission(session, user, submission_id)
    chain = []
    seen = set()
    for _ in range(100):
        if current.id in seen:
            raise HTTPException(status_code=409, detail="提交版本关系异常，请联系管理员")
        seen.add(current.id)
        chain.append({"id": current.id, "parent_submission_id": current.parent_submission_id,
                      "code": current.code, "stage": current.stage,
                      "verdict": effective_verdict(current), "tests_passed": current.tests_passed,
                      "created_at": current.created_at.isoformat() if current.created_at else None})
        if current.parent_submission_id is None:
            return list(reversed(chain))
        current = await accessible_submission(session, user, current.parent_submission_id)
    raise HTTPException(status_code=409, detail="版本链超过单次查看上限，请从较早版本查看")


