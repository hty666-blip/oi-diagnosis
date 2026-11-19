"""默认库尚未创建时，初始化脚本必须先连接到 MySQL 实例。"""
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


async def test_initialization_creates_missing_database_without_connecting_to_it_first(api_context, monkeypatch):
    url = make_url(os.environ["TEST_DATABASE_URL"])
    name = "test_initialization_" + uuid4().hex
    location = Path(__file__).resolve().parents[2] / "scripts/init_database.py"
    spec = importlib.util.spec_from_file_location("isolated_initialization", location)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.settings, "app_env", "dev")
    monkeypatch.setattr(module.settings, "database_url", url.set(database=name).render_as_string(hide_password=False))
    server = create_async_engine(url.set(database=""))
    try:
        await module.ensure_database()
        async with server.connect() as conn:
            assert await conn.scalar(text("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=:name"), {"name": name}) == name
    finally:
        async with server.begin() as conn:
            await conn.execute(text(f"DROP DATABASE IF EXISTS `{name}`"))
        await server.dispose()


async def test_initialization_rejects_production_before_connecting(monkeypatch):
    location = Path(__file__).resolve().parents[2] / "scripts/init_database.py"
    spec = importlib.util.spec_from_file_location("production_initialization", location)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.settings, "app_env", "production")
    with pytest.raises(RuntimeError, match="开发环境"):
        await module.ensure_database()
