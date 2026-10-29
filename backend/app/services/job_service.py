"""持久判题队列：短事务领取、续租与所有者隔离，不依赖内存任务列表。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.models import Diagnosis, Evidence, JudgeJob, Submission, User
from app.services.access import accessible_submission

ERROR_MESSAGES = {
    "execution_failed": "判题执行器暂时不可用，本次未完成验证。",
    "timeout": "本次判题超过任务时限，结果未完成验证。",
    "lease_expired": "执行服务中断，任务已重新进入恢复流程。",
    "interrupted": "执行服务正在停止，任务稍后重新执行。",
}


def failure_message(reason: str, *, retrying: bool, manual_retry_available: bool) -> str:
    if retrying:
        return ERROR_MESSAGES.get(reason, ERROR_MESSAGES["execution_failed"])
    cause = {
        "timeout": "本次判题超过任务时限",
        "interrupted": "判题执行中断",
        "lease_expired": "执行服务中断",
    }.get(reason, "判题执行服务暂时不可用")
    action = "可手动重试或联系教师检查服务。" if manual_retry_available else "请联系教师检查执行服务。"
    return f"{cause}，已达到自动尝试上限，结果未完成验证；{action}"


class LeaseLost(RuntimeError):
    """调用者已失去本次执行的写入权，不能更改提交或证据。"""


@dataclass(frozen=True)
class JobLease:
    job_id: int
    submission_id: int
    owner_token: str
    attempt: int
    committed: asyncio.Event = field(default_factory=asyncio.Event, compare=False, repr=False)


def job_out(job: JudgeJob | None, *, can_cancel: bool = False) -> dict | None:
    if job is None:
        return None
    return {
        "id": job.id, "status": job.status,
        "attempts": job.attempts, "max_attempts": job.max_attempts,
        "manual_retries": job.manual_retries,
        "retryable": job.status == "failed" and job.manual_retries < settings.judge_job_max_manual_retries,
        "cancelable": can_cancel and job.status in ("queued", "running"),
        "last_error": job.last_error,
        "available_at": job.available_at.isoformat(),
        "lease_expires_at": job.lease_expires_at.isoformat() if job.lease_expires_at else None,
    }


async def require_lease(session: AsyncSession, lease: JobLease) -> JudgeJob:
    """必须在任何待保存对象 flush 前验证令牌；锁与写入处于同一事务。"""
    with session.no_autoflush:
        job = (await session.execute(
            select(JudgeJob).where(
                JudgeJob.id == lease.job_id, JudgeJob.submission_id == lease.submission_id,
                JudgeJob.status == "running", JudgeJob.owner_token == lease.owner_token,
                JudgeJob.attempts == lease.attempt,
                JudgeJob.lease_expires_at > func.clock_timestamp(),
            ).with_for_update().execution_options(populate_existing=True)
        )).scalar_one_or_none()
    if job is None:
        raise LeaseLost("判题任务已由其他执行者接管")
    return job


class JobService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def claim(self) -> JobLease | None:
        """SKIP LOCKED 让不同工作器独立领取；过期租约可被下一轮接管。"""
        while True:
            job = (await self.session.execute(
                select(JudgeJob).where(or_(
                    and_(JudgeJob.status == "queued", JudgeJob.available_at <= func.clock_timestamp()),
                    and_(JudgeJob.status == "running", JudgeJob.lease_expires_at <= func.clock_timestamp()),
                )).order_by(JudgeJob.available_at, JudgeJob.id)
                .with_for_update(skip_locked=True).limit(1)
            )).scalar_one_or_none()
            if job is None:
                await self.session.rollback()
                return None
            submission = await self.session.get(Submission, job.submission_id)
            diagnosis = (await self.session.execute(
                select(Diagnosis).where(Diagnosis.submission_id == job.submission_id)
            )).scalar_one_or_none()
            # 已完成或人工审定的记录不可被恢复任务重新执行。
            if submission is None or submission.stage == "READY" or (diagnosis and diagnosis.reviewer_id is not None):
                job.status = "succeeded" if submission and submission.stage == "READY" else "failed"
                job.owner_token = None
                job.lease_expires_at = None
                job.finished_at = func.now()
                job.last_error = None if job.status == "succeeded" else "当前记录已有教师审定，不能重新执行。"
                await self.session.commit()
                continue
            if job.attempts >= job.max_attempts:
                job.status = "failed"
                job.owner_token = None
                job.lease_expires_at = None
                job.finished_at = func.now()
                job.last_error = failure_message("lease_expired", retrying=False,
                                                 manual_retry_available=job.manual_retries < settings.judge_job_max_manual_retries)
                submission.stage = "SANDBOX_ERROR"
                submission.verdict = None
                submission.tests_passed = None
                await self.session.commit()
                continue
            if job.status == "running":
                job.last_error = ERROR_MESSAGES["lease_expired"]
            job.status = "running"
            job.attempts += 1
            job.owner_token = uuid4().hex
            job.lease_expires_at = func.clock_timestamp() + timedelta(seconds=max(5, settings.judge_job_lease_seconds))
            job.finished_at = None
            submission.stage = "COMPILING"
            submission.verdict = None
            submission.tests_passed = None
            # 未完成的一次执行不可作为下一次尝试的证据。
            await self.session.execute(delete(Evidence).where(Evidence.submission_id == submission.id))
            await self.session.execute(delete(Diagnosis).where(Diagnosis.submission_id == submission.id))
            lease = JobLease(job.id, submission.id, job.owner_token, job.attempts)
            await self.session.commit()
            return lease

    async def renew(self, lease: JobLease) -> bool:
        result = await self.session.execute(
            update(JudgeJob).where(
                JudgeJob.id == lease.job_id, JudgeJob.status == "running",
                JudgeJob.owner_token == lease.owner_token,
                JudgeJob.attempts == lease.attempt, JudgeJob.lease_expires_at > func.clock_timestamp(),
            ).values(lease_expires_at=func.clock_timestamp() + timedelta(seconds=max(5, settings.judge_job_lease_seconds))))
        await self.session.commit()
        return result.rowcount == 1

    async def completed_by(self, lease: JobLease) -> bool:
        """完成后的清理仍属于原执行者，不能被心跳误判成失去所有权。"""
        return (await self.session.scalar(select(JudgeJob.id).where(
            JudgeJob.id == lease.job_id, JudgeJob.submission_id == lease.submission_id,
            JudgeJob.status == "succeeded", JudgeJob.owner_token == lease.owner_token,
            JudgeJob.attempts == lease.attempt,
        ))) is not None

    async def fail(self, lease: JobLease, reason: str) -> bool:
        try:
            job = await require_lease(self.session, lease)
        except LeaseLost:
            await self.session.rollback()
            return False
        pending = job.attempts < job.max_attempts
        job.status = "queued" if pending else "failed"
        job.owner_token = None
        job.lease_expires_at = None
        job.last_error = failure_message(reason, retrying=pending,
                                         manual_retry_available=job.manual_retries < settings.judge_job_max_manual_retries)
        job.available_at = func.clock_timestamp() + timedelta(seconds=min(2 ** job.attempts, 10) if pending else 0)
        job.finished_at = None if pending else func.now()
        await self.session.execute(update(Submission).where(Submission.id == lease.submission_id).values(
            stage="QUEUED" if pending else "SANDBOX_ERROR", verdict=None, tests_passed=None,
        ))
        await self.session.commit()
        return True

    async def retry(self, submission_id: int, user: User) -> JudgeJob:
        submission = await accessible_submission(self.session, user, submission_id)
        if user.role != "admin" and submission.student_id != user.id:
            raise HTTPException(status_code=403, detail="只能重试自己提交的判题任务")
        job = (await self.session.execute(
            select(JudgeJob).where(JudgeJob.submission_id == submission_id)
            .with_for_update().execution_options(populate_existing=True)
        )).scalar_one_or_none()
        if job is None or job.status != "failed" or submission.stage == "READY":
            raise HTTPException(status_code=409, detail="当前任务无需重试，请查看最新执行状态")
        diagnosis = (await self.session.execute(
            select(Diagnosis).where(Diagnosis.submission_id == submission_id)
        )).scalar_one_or_none()
        if diagnosis and diagnosis.reviewer_id is not None:
            raise HTTPException(status_code=409, detail="已有教师审定，请保留当前结果并提交新版本")
        if job.manual_retries >= settings.judge_job_max_manual_retries:
            raise HTTPException(status_code=409, detail="已达到重试次数上限，请联系教师检查执行服务")
        job.manual_retries += 1
        job.attempts = 0
        job.status = "queued"
        job.owner_token = None
        job.lease_expires_at = None
        job.available_at = func.clock_timestamp()
        job.finished_at = None
        submission.stage = "QUEUED"
        submission.verdict = None
        submission.tests_passed = None
        await self.session.commit()
        await self.session.refresh(job)
        return job

    async def cancel(self, submission_id: int, user: User) -> JudgeJob:
        submission = await accessible_submission(self.session, user, submission_id)
        if user.role != "admin" and submission.student_id != user.id:
            raise HTTPException(status_code=403, detail="只能取消自己提交的判题任务")
        job = (await self.session.execute(
            select(JudgeJob).where(JudgeJob.submission_id == submission_id)
            .with_for_update().execution_options(populate_existing=True)
        )).scalar_one_or_none()
        if job is None:
            raise HTTPException(status_code=409, detail="历史同步提交不支持取消，请以新后台任务重新提交")
        # 与工作器保持 Job -> Submission 锁序，并在等待 Job 锁后重读提交。
        submission = (await self.session.execute(
            select(Submission).where(Submission.id == submission_id)
            .execution_options(populate_existing=True)
        )).scalar_one()
        if job.status == "cancelled":
            await self.session.commit()
            return job
        if job.status not in ("queued", "running") or submission.stage == "READY":
            raise HTTPException(status_code=409, detail="任务已结束，请查看最新执行结果")
        diagnosis = (await self.session.execute(
            select(Diagnosis).where(Diagnosis.submission_id == submission_id)
        )).scalar_one_or_none()
        if diagnosis and diagnosis.reviewer_id is not None:
            raise HTTPException(status_code=409, detail="已有教师审定，不能取消或覆盖原结果")
        job.status = "cancelled"
        job.owner_token = None
        job.lease_expires_at = None
        job.last_error = None
        job.finished_at = func.clock_timestamp()
        submission.stage = "CANCELLED"
        submission.verdict = None
        submission.tests_passed = None
        await self.session.commit()
        await self.session.refresh(job)
        return job
