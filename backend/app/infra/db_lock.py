"""随事务释放的提交去重锁，不向连接池遗留会话级锁。"""

from sqlalchemy import text
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import SubmissionLock


async def acquire_submission_lock(session: AsyncSession, key: int) -> None:
    if session.get_bind().dialect.name == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
        return
    if session.get_bind().dialect.name != "mysql":
        raise RuntimeError("提交去重锁只支持 PostgreSQL 和 MySQL")
    statement = insert(SubmissionLock).values(lock_key=key)
    # INSERT 新行或 UPDATE 现有行均取得排他锁，自动随当前事务释放。
    await session.execute(statement.on_duplicate_key_update(lock_key=statement.inserted.lock_key))
