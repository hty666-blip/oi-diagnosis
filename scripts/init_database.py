"""创建 DATABASE_URL 指向的 MySQL 数据库；表结构仍由 Alembic 管理。"""

from __future__ import annotations

import asyncio
import re
import sys
from contextlib import suppress
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import settings


async def ensure_database() -> None:
    if settings.app_env.strip().lower() != "dev":
        raise RuntimeError("数据库初始化脚本仅允许开发环境")
    url = make_url(settings.database_url)
    if not url.drivername.startswith("mysql"):
        print(f"数据库初始化跳过：当前驱动为 {url.drivername}")
        return
    database = url.database or ""
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", database):
        raise RuntimeError("MySQL 数据库名必须由字母、数字或下划线组成")

    # URL.set(None) 表示保留旧值；空字符串才真正移除默认库。
    server_url = url.set(database="")
    engine = create_async_engine(server_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(
                f"CREATE DATABASE IF NOT EXISTS `{database}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            ))
    finally:
        await engine.dispose()
    print(f"MySQL 数据库已就绪：{url.host}:{url.port}/{database}")


def main() -> int:
    with suppress(AttributeError, OSError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    try:
        asyncio.run(ensure_database())
        return 0
    except Exception as exc:  # noqa: BLE001
        url = make_url(settings.database_url)
        target = f"{url.host}:{url.port}/{url.database}"
        print(
            f"数据库初始化失败：无法访问 {target}。"
            f"请确认服务已启动、端口可达且账号具有 CREATE DATABASE 权限。错误类型：{type(exc).__name__}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
