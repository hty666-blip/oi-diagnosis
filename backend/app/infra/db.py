"""异步数据库会话与基础操作。"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings


def database_engine_options(url: str) -> dict:
    if make_url(url).get_backend_name() == "postgresql":
        return {"connect_args": {"statement_cache_size": 0, "prepared_statement_cache_size": 0}}
    if make_url(url).get_backend_name() == "mysql":
        return {"isolation_level": "READ COMMITTED"}
    return {}


def configure_database_engine(target) -> None:
    if target.dialect.name != "mysql":
        return

    @event.listens_for(target.sync_engine, "connect")
    def use_utc(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("SET time_zone = '+00:00'")
        finally:
            cursor.close()


engine = create_async_engine(
    settings.database_url,
    # 不记录包含学生源码、执行输出和密码哈希的 SQL 参数。
    echo=False,
    hide_parameters=True,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    # 同时控制驱动与 SQLAlchemy 适配器的预编译缓存；迁移后仍需重启旧服务连接。
    **database_engine_options(settings.database_url),
)
configure_database_engine(engine)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def ping() -> bool:
    """探活：数据库连不上不应该让整个服务 500。"""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


async def dispose_engine() -> None:
    await engine.dispose()
