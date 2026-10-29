"""PostgreSQL 判题工作器，可由 API 生命周期或 python -m app.worker 运行。"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from app.core.config import settings
from app.domain.models import Submission
from app.infra.db import SessionLocal, dispose_engine
from app.services.job_service import JobLease, JobService, LeaseLost

logger = logging.getLogger(__name__)


class JudgeWorker:
    def __init__(self, session_factory=None) -> None:
        self.session_factory = session_factory or SessionLocal

    async def _execute_owned(self, lease: JobLease) -> None:
        from app.services.submission_service import SubmissionService

        async with self.session_factory() as session:
            submission = await session.get(Submission, lease.submission_id)
            if submission is None:
                raise LeaseLost("提交已不存在")
            await SubmissionService(session, lease=lease).run(submission)

    async def _heartbeat(self, lease: JobLease, execution: asyncio.Task, lost: asyncio.Event) -> None:
        interval = max(1.0, settings.judge_job_lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            if lease.committed.is_set():
                return
            try:
                async with self.session_factory() as session:
                    jobs = JobService(session)
                    owned = await jobs.renew(lease)
                    if not owned and await jobs.completed_by(lease):
                        return
            except Exception:
                # 数据库不可用时不假定自己仍拥有任务，停止写入并等待恢复。
                owned = False
            if not owned:
                lost.set()
                execution.cancel()
                return

    async def _fail(self, lease: JobLease, reason: str) -> None:
        async with asyncio.timeout(5):
            async with self.session_factory() as session:
                await JobService(session).fail(lease, reason)

    async def run_once(self) -> bool:
        async with self.session_factory() as session:
            lease = await JobService(session).claim()
        if lease is None:
            return False
        lost = asyncio.Event()
        execution = asyncio.create_task(self._execute_owned(lease))
        heartbeat = asyncio.create_task(self._heartbeat(lease, execution, lost))
        try:
            await asyncio.wait_for(execution, timeout=max(1.0, settings.judge_job_timeout_seconds))
        except LeaseLost:
            pass
        except TimeoutError:
            await self._fail(lease, "timeout")
        except asyncio.CancelledError:
            if not lost.is_set() or asyncio.current_task().cancelling():
                try:
                    await self._fail(lease, "interrupted")
                except Exception as exc:
                    # 不能让关停时的数据库故障吞掉取消，恢复由租约到期承担。
                    logger.warning("判题关停状态暂未保存：job=%s type=%s", lease.job_id, type(exc).__name__)
                raise
        except Exception as exc:
            logger.warning("判题任务执行失败：job=%s type=%s", lease.job_id, type(exc).__name__)
            await self._fail(lease, "execution_failed")
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        return True

    async def run_forever(self) -> None:
        while True:
            try:
                processed = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("判题工作器暂时不可用：%s", type(exc).__name__)
                processed = False
            if not processed:
                await asyncio.sleep(max(0.1, settings.judge_worker_poll_seconds))


async def main() -> None:
    from app.core.security import validate_security_settings

    validate_security_settings()
    try:
        await JudgeWorker().run_forever()
    finally:
        await dispose_engine()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    with suppress(KeyboardInterrupt):
        asyncio.run(main())
