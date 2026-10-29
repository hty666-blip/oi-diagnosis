"""按授权范围汇总判题任务，只返回数据库可观测事实。"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.domain.models import JudgeJob, Problem, Submission, User
from app.services.access import submission_scope
from app.services.job_service import ERROR_MESSAGES

JOB_STATUSES = ("queued", "running", "failed", "cancelled", "succeeded")
GENERIC_FAILURE = "执行服务需要检查。"


def safe_failure_message(value: str | None) -> str:
    """历史记录可能含旧执行器文本，不能把原始服务错误重新暴露给页面。"""
    return value if value in ERROR_MESSAGES.values() else GENERIC_FAILURE


class OperationsService:
    def __init__(self, session: AsyncSession, user: User) -> None:
        self.session = session
        self.user = user

    async def judging(self) -> dict:
        if self.user.role not in ("teacher", "admin"):
            raise HTTPException(status_code=403, detail="只有教师或管理员可以查看判题队列")
        observed_at = await self.session.scalar(select(func.clock_timestamp()))
        status_counts = [
            func.count(JudgeJob.id).filter(JudgeJob.status == status).label(status)
            for status in JOB_STATUSES
        ]
        running = JudgeJob.status == "running"
        has_owner = func.nullif(func.trim(JudgeJob.owner_token), "").is_not(None)
        has_lease = and_(has_owner, JudgeJob.lease_expires_at.is_not(None))
        aggregation = (await self.session.execute(
            select(
                *status_counts,
                func.max(func.greatest(
                    func.extract("epoch", observed_at - JudgeJob.available_at), 0
                )).filter(JudgeJob.status == "queued").label("oldest_wait"),
                func.count(JudgeJob.id).filter(
                    running, has_lease, JudgeJob.lease_expires_at > observed_at
                ).label("active_leases"),
                func.count(JudgeJob.id).filter(
                    running, has_lease, JudgeJob.lease_expires_at <= observed_at
                ).label("expired_leases"),
                func.count(JudgeJob.id).filter(
                    running, or_(~has_owner, JudgeJob.lease_expires_at.is_(None))
                ).label("missing_leases"),
            )
            .select_from(JudgeJob)
            .join(Submission, Submission.id == JudgeJob.submission_id)
            .where(submission_scope(self.user))
        )).one()

        student_user = aliased(User)
        recent = (await self.session.execute(
            select(JudgeJob, Submission, Problem, student_user)
            .select_from(JudgeJob)
            .join(Submission, Submission.id == JudgeJob.submission_id)
            .join(Problem, Problem.id == Submission.problem_id)
            .outerjoin(student_user, student_user.id == Submission.student_id)
            .where(JudgeJob.status == "failed", submission_scope(self.user))
            .order_by(JudgeJob.finished_at.desc().nulls_last(), JudgeJob.id.desc())
            .limit(5)
        )).all()
        kind = "all" if self.user.role == "admin" else (
            "class_and_own" if (self.user.class_name or "").strip() else "own"
        )
        return {
            "observed_at": observed_at.isoformat(),
            "scope": {"kind": kind, "class_name": self.user.class_name if kind == "class_and_own" else None},
            "counts": {status: int(getattr(aggregation, status)) for status in JOB_STATUSES},
            "oldest_queued_wait_seconds": (
                float(aggregation.oldest_wait) if aggregation.oldest_wait is not None else None
            ),
            "leases": {"active": int(aggregation.active_leases),
                       "expired": int(aggregation.expired_leases),
                       "missing": int(aggregation.missing_leases)},
            "recent_failures": [
                {"submission_id": submission.id, "problem_pid": problem.pid,
                 "problem_title": problem.title, "student_id": submission.student_id,
                 "student_name": (student.display_name or student.username) if student else "",
                 "attempts": job.attempts, "last_error": safe_failure_message(job.last_error),
                 "finished_at": job.finished_at.isoformat() if job.finished_at else None}
                for job, submission, problem, student in recent
            ],
        }
