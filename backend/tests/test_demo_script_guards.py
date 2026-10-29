import importlib.util
from pathlib import Path

import pytest


async def test_demo_script_rejects_production_before_opening_database(monkeypatch):
    location = Path(__file__).resolve().parents[2] / "scripts" / "seed_demo.py"
    spec = importlib.util.spec_from_file_location("guarded_demo_script", location)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.settings, "app_env", "production")

    def forbidden():
        raise AssertionError("生产环境不应打开数据库来生成公开演示账号")

    monkeypatch.setattr(module, "SessionLocal", forbidden)
    with pytest.raises(RuntimeError, match="只允许"):
        await module.ensure_users()
