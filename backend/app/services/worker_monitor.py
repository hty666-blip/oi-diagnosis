"""数据库记录工作器活动，不把任务队列或执行租约当作进程心跳。"""
from uuid import uuid4

from sqlalchemy import func, select

from app.core.config import settings
from app.domain.models import WorkerHeartbeat


class WorkerMonitor:
    def __init__(self, session_factory, kind: str):
        self.sessions = session_factory
        self.id = str(uuid4())
        self.kind = kind
        self.state = "starting"
        self.current_job = None

    async def pulse(self):
        async with self.sessions() as session:
            row = await session.get(WorkerHeartbeat, self.id)
            if row is None:
                row = WorkerHeartbeat(id=self.id, kind=self.kind)
                session.add(row)
            row.state, row.current_job = self.state, self.current_job
            row.seen_at = func.clock_timestamp()
            await session.commit()


async def worker_status(session):
    now = await session.scalar(select(func.clock_timestamp()))
    rows = (await session.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.seen_at.desc()).limit(30))).all()
    threshold = max(15, int(settings.judge_worker_heartbeat_seconds * 3))
    return {"observed_at": now, "stale_after_seconds": threshold,
            "items": [{"id": r.id, "kind": r.kind, "state": r.state, "current_job": r.current_job,
                       "seen_at": r.seen_at, "age_seconds": max(0, (now - r.seen_at).total_seconds()),
                       "online": r.state != "stopped" and 0 <= (now - r.seen_at).total_seconds() <= threshold}
                      for r in rows],
            "api_judge_enabled": settings.judge_worker_enabled,
            "maintenance_enabled": settings.maintenance_worker_enabled}
