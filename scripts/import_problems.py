#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 data/problems/*.json 导入 PostgreSQL 的 problem 表（幂等 upsert）。

用法（在 backend 目录下）:
    ..\\.venv\\Scripts\\python ..\\scripts\\import_problems.py
    ..\\.venv\\Scripts\\python ..\\scripts\\import_problems.py --dir ..\\data\\problems
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# 允许从仓库根目录运行
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.domain.models import Problem  # noqa: E402
from app.infra.db import SessionLocal, dispose_engine  # noqa: E402


async def upsert(path: Path) -> tuple[str, bool]:
    rec = json.loads(path.read_text(encoding="utf-8"))
    pid = rec["pid"]
    async with SessionLocal() as s:
        existing = (await s.execute(select(Problem).where(Problem.pid == pid))).scalar_one_or_none()
        created = existing is None
        obj = existing or Problem(pid=pid)
        obj.title = rec.get("title", "")
        obj.statement_md = rec.get("markdown", "")
        obj.samples = rec.get("samples") or []
        obj.difficulty = rec.get("difficulty", "")
        obj.tags = rec.get("tags") or []
        obj.package_level = rec.get("package_level", "C")
        obj.has_testdata = bool(rec.get("has_testdata", False))
        obj.source = rec.get("source", "")
        obj.source_url = rec.get("source_url", "")
        obj.license_note = rec.get("license_note", "")
        if created:
            s.add(obj)
        await s.commit()
    return pid, created


async def main_async(directory: Path) -> int:
    files = sorted(directory.glob("*.json"))
    if not files:
        print(f"目录里没有 json: {directory.resolve()}")
        return 1
    created = updated = failed = 0
    for f in files:
        try:
            pid, is_new = await upsert(f)
            created += int(is_new)
            updated += int(not is_new)
            print(f"[{'new' if is_new else 'upd'}] {pid}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[fail] {f.name}: {type(e).__name__}: {e}")
    print(f"\n完成: new={created} updated={updated} failed={failed} total={len(files)}")
    await dispose_engine()
    return 0 if failed == 0 else 1


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(ROOT / "data" / "problems"))
    args = ap.parse_args()
    return asyncio.run(main_async(Path(args.dir)))


if __name__ == "__main__":
    raise SystemExit(main())
