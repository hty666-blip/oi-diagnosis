"""题库与教练台接口。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.api.deps import CurrentUser, TeacherUser
from app.core.text_validation import StorageText
from app.domain.models import Diagnosis, Problem, ProblemVersion, Submission, TrainingProblem, User
from app.infra.db import get_session
from app.services.access import accessible_submission, submission_scope
from app.services.judgement import effective_verdict, result_narrative, valid_test_summary
from app.services.problem_versions import resolve_many, resolve_problem
from app.services.training_service import accessible_training

router = APIRouter(tags=["catalog"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/problems")
async def list_problems(
    session: SessionDep,
    user: CurrentUser,
    q: Annotated[StorageText, Query(max_length=120, description="按题号/标题模糊搜索")] = "",
    limit: int = Query(50, ge=1, le=200),
) -> list[dict]:
    stmt = select(Problem).where(Problem.is_active.is_(True))
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
            "verification_scope": "package" if r.has_testdata and r.package_level in ("A", "B") else "samples",
            "source": r.source,
            "revision": r.revision,
        }
        for r in rows
    ]


@router.get("/coach/queue")
async def coach_queue(
    session: SessionDep,
    user: TeacherUser,
    limit: int = Query(20, ge=1, le=100),
    before_id: int | None = Query(None, gt=0, le=2**63 - 1),
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
            .where(Diagnosis.status == "needs_human", submission_scope(user),
                   True if before_id is None else Diagnosis.id < before_id)
            .order_by(Diagnosis.id.desc())
            .limit(limit)
        )
    ).all()
    out = []
    frozen = await resolve_many(session, [(p, sub.problem_version_id) for _, sub, p, _ in rows])
    for (diag, sub, _, student), problem in zip(rows, frozen, strict=True):
        out.append({
            "diagnosis_id": diag.id,
            "submission_id": sub.id,
            "verdict": effective_verdict(sub),
            "route": (diag.model_meta or {}).get("route"),
            "confidence": None if (diag.model_meta or {}).get("confidence_source") == "unscored" else diag.confidence,
            "narrative": result_narrative(sub, diag.narrative),
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
    before_id: int | None = Query(None, gt=0, le=2**63 - 1),
) -> list[dict]:
    """当前学生自己的提交历史（含诊断摘要）。"""
    rows = (
        await session.execute(
            select(Submission, Diagnosis, Problem)
            .outerjoin(Diagnosis, Diagnosis.submission_id == Submission.id)
            .join(Problem, Problem.id == Submission.problem_id)
            .where(Submission.student_id == user.id,
                   True if before_id is None else Submission.id < before_id)
            .order_by(Submission.id.desc())
            .limit(limit)
        )
    ).all()
    out = []
    frozen = await resolve_many(session, [(p, sub.problem_version_id) for sub, _, p in rows])
    for (sub, diag, _), problem in zip(rows, frozen, strict=True):
        out.append({
            "id": sub.id,
            "problem_id": sub.problem_id,
            "parent_submission_id": sub.parent_submission_id,
            "problem_pid": problem.pid,
            "problem_title": problem.title,
            "stage": sub.stage,
            "language": sub.lang,
            "tests_passed": valid_test_summary(sub),
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
    verdict: dict[str, int] = {}
    # 分批读取轻量计数，不把全班源码和诊断协作正文加载进概览内存。
    stream = await session.stream(select(Submission.verdict, Submission.tests_passed).where(submission_scope(user)))
    async for row in stream:
        key = effective_verdict(Submission(verdict=row.verdict, tests_passed=row.tests_passed)) or "?"
        verdict[key] = verdict.get(key, 0) + 1
    await stream.close()
    route: dict[str, int] = {}
    route_field = Diagnosis.model_meta["route"].as_string()
    groups = (await session.execute(select(route_field, func.count()).join(
        Submission, Submission.id == Diagnosis.submission_id).where(submission_scope(user)).group_by(route_field))).all()
    for key, count in groups:
        name = key or "unknown"
        route[name] = route.get(name, 0) + count
    pending = await session.scalar(select(func.count()).select_from(Diagnosis).join(
        Submission, Submission.id == Diagnosis.submission_id).where(submission_scope(user), Diagnosis.status == "needs_human"))
    return {
        "submissions": sum(verdict.values()),
        "diagnoses": sum(route.values()),
        "pending_review": pending,
        "verdict": verdict,
        "route": route,
    }

@router.get("/problems/{pid}")
async def get_problem(
    pid: Annotated[StorageText, Path(min_length=1, max_length=32)],
    session: SessionDep,
    user: CurrentUser,
    training_id: int | None = Query(None, gt=0, le=2**63 - 1),
    submission_id: int | None = Query(None, gt=0, le=2**63 - 1),
) -> dict:
    """题目详情：题面 Markdown + 样例 + 来源。"""
    row = (
        await session.execute(select(Problem).where(Problem.pid == pid))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    vid = None
    if training_id is not None and submission_id is not None:
        raise HTTPException(422, "请选择训练或历史提交中的一种题目上下文")
    if training_id is not None:
        await accessible_training(session, user, training_id)
        item = await session.get(TrainingProblem, (training_id, row.id))
        if item is None:
            raise HTTPException(404, "题目不属于当前训练")
        vid = item.problem_version_id
    elif submission_id is not None:
        sub = await accessible_submission(session, user, submission_id)
        if sub.problem_id != row.id:
            raise HTTPException(404, "题目不属于当前提交")
        vid = sub.problem_version_id
    elif not row.is_active:
        raise HTTPException(404, "题目不存在或已下架")
    row = await resolve_problem(session, row.id, vid)
    version_id = getattr(row, "version_id", None)
    version = await session.get(ProblemVersion, version_id) if version_id else None
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
        "verification_scope": (version.suite or {}).get("scope", "samples") if version else
                              "unknown" if row.has_testdata else "samples",
        "license_note": row.license_note,
        "revision": row.revision,
        "version_id": version_id,
        "time_limit_ms": version.suite["time_limit_ms"] if version and version.suite else None,
        "memory_limit_mb": version.suite["memory_limit_mb"] if version and version.suite else None,
    }
