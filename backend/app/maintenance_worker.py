"""可恢复的备份/恢复验证队列，失效执行者不能发布文件或验证结果。"""
import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update

from app.domain.models import BackupArtifact, MaintenanceTask, User
from app.infra.db import SessionLocal
from app.infra.sql_types import after_seconds
from app.services.backup_service import BackupError, BackupIO
from app.services.worker_monitor import WorkerMonitor


class MaintenanceWorker:
    def __init__(self, sessions=None, io=None):
        self.sessions = sessions or SessionLocal
        self.io = io or BackupIO()
        self.monitor = WorkerMonitor(self.sessions, "maintenance")

    async def claim(self):
        async with self.sessions() as session:
            while True:
                row = await session.scalar(select(MaintenanceTask).where(or_(
                    MaintenanceTask.status == "queued", and_(MaintenanceTask.status == "running", MaintenanceTask.lease_until <= func.clock_timestamp())))
                    .order_by(MaintenanceTask.created_at).with_for_update(skip_locked=True).limit(1))
                if row is None:
                    return None
                user = await session.get(User, row.actor_id)
                if row.attempts >= 3 or user is None or not user.enabled or user.role != "admin" or user.token_version != row.actor_version:
                    row.status, row.error, row.owner, row.lease_until = "failed", "attempts_exhausted" if row.attempts >= 3 else "permission_changed", None, None
                    row.finished_at = func.clock_timestamp()
                    await session.commit()
                    continue
                row.status, row.owner = "running", str(uuid4())
                row.attempts += 1
                row.lease_until = after_seconds(90)
                await session.commit()
                return row.id, row.owner

    async def _lease(self, task_id, owner, execution):
        while True:
            await asyncio.sleep(10)
            try:
                async with self.sessions() as session:
                    changed = await session.execute(update(MaintenanceTask).where(
                        MaintenanceTask.id == task_id, MaintenanceTask.owner == owner, MaintenanceTask.status == "running",
                        MaintenanceTask.lease_until > func.clock_timestamp()).values(lease_until=after_seconds(90)))
                    await session.commit()
                    if changed.rowcount != 1:
                        execution.cancel()
                        return
            except Exception:
                execution.cancel()
                return

    async def execute(self, task_id, owner):
        async with self.sessions() as session:
            row = await session.get(MaintenanceTask, task_id)
            artifact = await session.get(BackupArtifact, row.artifact_id)
            kind, artifact_id = row.kind, artifact.id
        result, error = {}, ""
        try:
            async with asyncio.timeout(180):
                result = await (self.io.create(artifact_id, owner) if kind == "backup" else self.io.verify(artifact))
        except TimeoutError:
            error = "task_timeout"
        except BackupError as exc:
            error = str(exc)
        except Exception:
            error = "backup_failed" if kind == "backup" else "verify_failed"
        async with self.sessions() as session:
            row = await session.scalar(select(MaintenanceTask).where(MaintenanceTask.id == task_id,
                MaintenanceTask.owner == owner, MaintenanceTask.status == "running",
                MaintenanceTask.lease_until > func.clock_timestamp()).with_for_update())
            if row is None:
                return
            artifact = await session.get(BackupArtifact, artifact_id)
            if not error and kind == "backup":
                for key, value in result.items():
                    setattr(artifact, key, value)
            elif not error:
                artifact.verified_at = datetime.now(UTC)
            row.status, row.error, row.result = "failed" if error else "succeeded", error[:64], result if not error else {}
            row.finished_at, row.lease_until = func.clock_timestamp(), None
            await session.commit()

    async def run_once(self):
        claimed = await self.claim()
        if claimed is None:
            return False
        task_id, owner = claimed
        self.monitor.state, self.monitor.current_job = "busy", task_id
        execution = asyncio.create_task(self.execute(task_id, owner))
        lease = asyncio.create_task(self._lease(task_id, owner, execution))
        try:
            await execution
        finally:
            lease.cancel()
            with suppress(asyncio.CancelledError):
                await lease
            self.monitor.state, self.monitor.current_job = "idle", None
        return True

    async def _pulse(self):
        while True:
            with suppress(Exception):
                await self.monitor.pulse()
            await asyncio.sleep(5)

    async def run_forever(self):
        pulse = asyncio.create_task(self._pulse())
        self.monitor.state = "idle"
        try:
            while True:
                try:
                    done = await self.run_once()
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                    done = False
                except Exception:
                    done = False
                if not done:
                    await asyncio.sleep(1)
        finally:
            pulse.cancel()
            with suppress(asyncio.CancelledError):
                await pulse
            self.monitor.state, self.monitor.current_job = "stopped", None
            with suppress(Exception):
                await self.monitor.pulse()
