"""预览或清理过期的判题目录；默认不删除，Docker 状态不明时拒绝执行。"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.capabilities.docker_runner import RUN_ROOT


def _has_running_jobs() -> bool:
    try:
        result = subprocess.run(["docker", "ps", "--filter", "name=oi-run-", "--format", "{{.ID}}"],
                                capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("无法确认执行器状态，未清理任何目录") from exc
    if result.returncode:
        raise RuntimeError("无法确认执行器状态，未清理任何目录")
    return bool(result.stdout.strip())


def clean_stale(root: Path = RUN_ROOT, *, workspace: Path = ROOT, hours=24, execute=False, now=None):
    root, workspace = root.resolve(), workspace.resolve()
    if not root.is_relative_to(workspace) or root == workspace or hours < 24:
        raise ValueError("仅允许清理项目内至少24小时前的专用判题缓存")
    cutoff = (time.time() if now is None else now) - hours * 3600

    def eligible(path):
        return (not path.is_symlink() and path.is_dir() and path.name.startswith("job-")
                and path.resolve().parent == root and path.resolve().is_relative_to(workspace)
                and path.stat().st_mtime < cutoff)

    candidates = [path for path in root.glob("job-*") if eligible(path)]
    if execute and candidates:
        if _has_running_jobs():
            raise RuntimeError("仍有判题容器执行，请等待任务结束后再清理；目录未改动")
        for path in candidates:
            # 删除前再次校验解析后的绝对位置、类型和年龄，不跟随目录链接。
            if not eligible(path):
                raise RuntimeError("目录状态已变化，已停止清理")
            target = path.resolve()
            if target.parent != root or not target.is_relative_to(workspace):
                raise RuntimeError("目录位置已变化，已停止清理")
            shutil.rmtree(target)
    return [path.name for path in candidates]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="实际清理；未指定时仅预览")
    parser.add_argument("--hours", type=int, default=24, help="最小年龄，至少24小时")
    args = parser.parse_args()
    names = clean_stale(hours=args.hours, execute=args.execute)
    print(f"{'已清理' if args.execute else '可清理（预览）'} {len(names)} 个过期目录")
    for name in names:
        print(name)


if __name__ == "__main__":
    with suppress(AttributeError, OSError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
