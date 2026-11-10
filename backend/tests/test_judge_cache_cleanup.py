import importlib.util
import os
from pathlib import Path

import pytest


@pytest.fixture
def cache_tool():
    spec = importlib.util.spec_from_file_location("judge_cache_tool", Path(__file__).resolve().parents[2] / "scripts/clean_judge_cache.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def folders(tmp_path):
    root = tmp_path / "runs"
    root.mkdir()
    old, fresh, other = root / "job-old", root / "job-new", root / "private"
    for item in (old, fresh, other):
        item.mkdir()
    os.utime(old, (0, 0))
    return root, old, fresh, other


def test_preview_does_not_delete_and_execution_keeps_fresh_and_unrelated(cache_tool, tmp_path, monkeypatch):
    root, old, fresh, other = folders(tmp_path)
    assert cache_tool.clean_stale(root, workspace=tmp_path) == ["job-old"]
    assert old.exists()
    monkeypatch.setattr(cache_tool, "_has_running_jobs", lambda: False)
    assert cache_tool.clean_stale(root, workspace=tmp_path, execute=True) == ["job-old"]
    assert not old.exists() and fresh.exists() and other.exists()


def test_running_or_unknown_executor_blocks_deletion(cache_tool, tmp_path, monkeypatch):
    root, old, _, _ = folders(tmp_path)
    monkeypatch.setattr(cache_tool, "_has_running_jobs", lambda: True)
    with pytest.raises(RuntimeError, match="仍有判题"):
        cache_tool.clean_stale(root, workspace=tmp_path, execute=True)
    assert old.exists()
    def unavailable():
        raise RuntimeError("无法确认执行器状态")
    monkeypatch.setattr(cache_tool, "_has_running_jobs", unavailable)
    with pytest.raises(RuntimeError, match="无法确认"):
        cache_tool.clean_stale(root, workspace=tmp_path, execute=True)
    assert old.exists()


def test_root_escape_and_recent_age_are_rejected(cache_tool, tmp_path):
    with pytest.raises(ValueError):
        cache_tool.clean_stale(tmp_path, workspace=tmp_path)
    with pytest.raises(ValueError):
        cache_tool.clean_stale(tmp_path.parent, workspace=tmp_path)
    with pytest.raises(ValueError):
        cache_tool.clean_stale(tmp_path / "runs", workspace=tmp_path, hours=1)
