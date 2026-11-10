"""训练按发布名单和显式提交绑定统计；不从历史自由练习推断完成情况。"""
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import (
    JudgeJob,
    Problem,
    Submission,
    Training,
    TrainingMember,
    TrainingProblem,
    User,
)
from app.services.class_access import same_class
from app.services.job_service import job_out
from app.services.judgement import effective_verdict, valid_test_summary
from app.services.problem_versions import resolve_many


def training_out(row: Training, *, include_audience=False) -> dict:
    out = {key: getattr(row, key) for key in (
        "id", "teacher_id", "original_teacher_id", "class_id", "class_name", "title", "objective", "description", "status",
        "due_at", "published_at", "created_at", "source_training_id",
    )}
    if include_audience:
        out["student_ids"] = row.student_ids
    return out


async def accessible_training(session: AsyncSession, user: User, training_id: int, *, lock=False) -> Training:
    stmt = select(Training).where(Training.id == training_id)
    if lock:
        stmt = stmt.with_for_update()
    row = (await session.scalars(stmt)).one_or_none()
    allowed = row is not None
    if row and user.role == "teacher":
        allowed = row.teacher_id == user.id and await same_class(session, user, row)
    elif row and user.role == "student":
        allowed = (row.status == "published" and await same_class(session, user, row)
                   and await session.get(TrainingMember, (row.id, user.id)) is not None)
    elif user.role != "admin":
        allowed = False
    if not allowed:
        raise HTTPException(404, "训练不存在或无权访问")
    return row


async def validate_training_submission(session: AsyncSession, user_id: int, training_id: int, problem_id: int):
    user = await session.get(User, user_id)
    if user is None or user.role != "student":
        raise HTTPException(403, "训练提交仅限名单中的学生")
    row = await accessible_training(session, user, training_id, lock=True)
    if await session.get(TrainingProblem, (row.id, problem_id)) is None:
        raise HTTPException(422, "这道题不属于当前训练")
    return row


def submission_cell(sub: Submission, job: JudgeJob | None, due_at: datetime, attempts: int) -> dict:
    verdict = effective_verdict(sub)
    state = ("cancelled" if job and job.status == "cancelled" else
             "failed" if (job and job.status == "failed") or sub.stage == "SANDBOX_ERROR" else
             "pending" if sub.stage != "READY" or (job and job.status in ("queued", "running")) else "complete")
    return {"id": sub.id, "stage": sub.stage, "verdict": verdict,
            "tests_passed": valid_test_summary(sub), "job": job_out(job),
            "state": state, "passed": state == "complete" and verdict == "AC",
            "late": sub.created_at > due_at, "attempts": attempts}


async def training_detail(session: AsyncSession, user: User, row: Training) -> dict:
    problems = (await session.execute(
        select(TrainingProblem, Problem).join(Problem, Problem.id == TrainingProblem.problem_id)
        .where(TrainingProblem.training_id == row.id).order_by(TrainingProblem.position)
    )).all()
    frozen = await resolve_many(session, [(p, item.problem_version_id) for item, p in problems])
    problems = [(item, p) for (item, _), p in zip(problems, frozen, strict=True)]
    members = (await session.scalars(
        select(User).join(TrainingMember, TrainingMember.student_id == User.id)
        .where(TrainingMember.training_id == row.id, User.role == "student",
               User.class_id == row.class_id if row.class_id is not None else
               (User.class_id.is_(None) & (User.class_name == row.class_name)))
        .order_by(User.id)
    )).all()
    if user.role == "student":
        members = [member for member in members if member.id == user.id]
    ids = [member.id for member in members]
    records = (await session.execute(
        select(Submission, JudgeJob).outerjoin(JudgeJob, JudgeJob.submission_id == Submission.id)
        .where(Submission.training_id == row.id, Submission.student_id.in_(ids))
        .order_by(Submission.id)
    )).all() if ids else []
    cells = {}
    for sub, job in records:
        key = (sub.student_id, sub.problem_id)
        attempts = cells.get(key, {}).get("attempts", 0) + 1
        cells[key] = submission_cell(sub, job, row.due_at, attempts)
    required = [item.problem_id for item, _ in problems if item.required]
    rows = [{"student_id": member.id, "name": member.display_name or member.username,
             "cells": [cells.get((member.id, item.problem_id)) for item, _ in problems],
             "complete": bool(required) and all(cells.get((member.id, pid), {}).get("passed") for pid in required)}
            for member in members]
    return {**training_out(row, include_audience=user.role != "student"), "problems": [
        {"pid": problem.pid, "title": problem.title, "required": item.required,
         "package_level": problem.package_level, "version_id": item.problem_version_id,
         "revision": getattr(problem, "revision", 0)} for item, problem in problems
    ], "rows": rows, "summary": {
        "students": len(rows), "participated": sum(any(r["cells"]) for r in rows),
        "completed": sum(r["complete"] for r in rows),
        "late_submissions": sum(sub.created_at > row.due_at for sub, _ in records),
    }, "observed_at": datetime.now(UTC)}
