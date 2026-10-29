"""题库与教练台接口。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.api.deps import CurrentUser, TeacherUser
from app.core.text_validation import StorageText
from app.domain.models import Diagnosis, Problem, Submission, User
from app.infra.db import get_session
from app.services.access import submission_scope
from app.services.judgement import effective_verdict, result_narrative

router = APIRouter(tags=["catalog"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/problems")
async def list_problems(
    session: SessionDep,
    user: CurrentUser,
    q: Annotated[StorageText, Query(max_length=120, description="按题号/标题模糊搜索")] = "",
    limit: int = Query(50, ge=1, le=200),
) -> list[dict]:
    stmt = select(Problem)
    if q:
        stmt = stmt.where(or_(
            Problem.pid.icontains(q, autoescape=True),
            Problem.title.icontains(q, autoescape=True),
        ))
    rows = (await session.execute(stmt.order_by(Problem.id).limit(limit))).scalars().all()
    return [
        {
            "pid": r.pid,
            "title": r.title,
            "difficulty": r.difficulty,
            "tags": r.tags,
            "samples": len(r.samples or []),
            "package_level": r.package_level,
            "source": r.source,
        }
        for r in rows
    ]


@router.get("/coach/queue")
async def coach_queue(
    session: SessionDep,
    user: TeacherUser,
    limit: int = Query(20, ge=1, le=100),
) -> list[dict]:
    """待人工复核队列：needs_human 的诊断，按时间倒序。"""
    student_user = aliased(User)
    rows = (
        await session.execute(
            select(Diagnosis, Submission, Problem, student_user)
            .select_from(Diagnosis)
            .join(Submission, Submission.id == Diagnosis.submission_id)
            .join(Problem, Problem.id == Submission.problem_id)
            .outerjoin(student_user, student_user.id == Submission.student_id)
            .where(Diagnosis.status == "needs_human", submission_scope(user))
            .order_by(Diagnosis.id.desc())
            .limit(limit)
        )
    ).all()
    out = []
    for diag, sub, problem, student in rows:
        out.append({
            "diagnosis_id": diag.id,
            "submission_id": sub.id,
            "verdict": effective_verdict(sub),
            "route": (diag.model_meta or {}).get("route"),
            "confidence": diag.confidence,
            "narrative": diag.narrative,
            "mechanism": diag.mechanism,
            "student_id": sub.student_id,
            "student_name": (student.display_name or student.username) if student else "",
            "problem_pid": problem.pid,
            "problem_title": problem.title,
            "review_revision": diag.review_revision,
            "reviewer_id": diag.reviewer_id,
            "reviewed_at": diag.reviewed_at.isoformat() if diag.reviewed_at else None,
        })
    return out

@router.get("/me/submissions")
async def my_submissions(
    session: SessionDep,
    user: CurrentUser,
    limit: int = Query(50, ge=1, le=200),
) -> list[dict]:
    """当前学生自己的提交历史（含诊断摘要）。"""
    rows = (
        await session.execute(
            select(Submission, Diagnosis, Problem)
            .outerjoin(Diagnosis, Diagnosis.submission_id == Submission.id)
            .join(Problem, Problem.id == Submission.problem_id)
            .where(Submission.student_id == user.id)
            .order_by(Submission.id.desc())
            .limit(limit)
        )
    ).all()
    out = []
    for sub, diag, problem in rows:
        out.append({
            "id": sub.id,
            "problem_id": sub.problem_id,
            "parent_submission_id": sub.parent_submission_id,
            "problem_pid": problem.pid,
            "problem_title": problem.title,
            "stage": sub.stage,
            "tests_passed": sub.tests_passed,
            "verdict": effective_verdict(sub),
            "created_at": sub.created_at.isoformat() if sub.created_at else "",
            "narrative": result_narrative(sub, diag.narrative if diag else "")[:120],
            "route": (diag.model_meta or {}).get("route") if diag else None,
            "p": (diag.nature or [{}])[0].get("p") if diag and diag.nature else None,
            "review_revision": diag.review_revision if diag else 0,
            "diagnosis_status": diag.status if diag else None,
            "reviewer_id": diag.reviewer_id if diag else None,
            "reviewed_at": diag.reviewed_at.isoformat() if diag and diag.reviewed_at else None,
        })
    return out


@router.get("/coach/overview")
async def coach_overview(session: SessionDep, user: TeacherUser) -> dict:
    """教练台概览：判定分布、路由分布、待复核数。"""
    subs = (
        await session.execute(select(Submission).where(submission_scope(user)))
    ).scalars().all()
    diags = (
        await session.execute(
            select(Diagnosis).join(Submission, Submission.id == Diagnosis.submission_id)
            .where(submission_scope(user))
        )
    ).scalars().all()
    verdict: dict[str, int] = {}
    for s in subs:
        key = effective_verdict(s) or "?"
        verdict[key] = verdict.get(key, 0) + 1
    route: dict[str, int] = {}
    for d in diags:
        r = (d.model_meta or {}).get("route", "unknown")
        route[r] = route.get(r, 0) + 1
    return {
        "submissions": len(subs),
        "diagnoses": len(diags),
        "pending_review": sum(1 for d in diags if d.status == "needs_human"),
        "verdict": verdict,
        "route": route,
    }

@router.get("/problems/{pid}")
async def get_problem(
    pid: Annotated[StorageText, Path(min_length=1, max_length=32)],
    session: SessionDep,
    user: CurrentUser,
) -> dict:
    """题目详情：题面 Markdown + 样例 + 来源。"""
    row = (
        await session.execute(select(Problem).where(Problem.pid == pid))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    return {
        "pid": row.pid,
        "title": row.title,
        "difficulty": row.difficulty,
        "tags": row.tags,
        "samples": row.samples or [],
        "statement_md": row.statement_md,
        "source": row.source,
        "source_url": row.source_url,
        "package_level": row.package_level,
        "time_limit_ms": 1000,
    }
