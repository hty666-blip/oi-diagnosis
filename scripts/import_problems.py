#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 data/problems/*.json 导入 DATABASE_URL 的 problem 表（幂等 upsert）。

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

from app.infra.db import SessionLocal, dispose_engine  # noqa: E402
from app.services.admin_service import management_lock  # noqa: E402
from app.services.problem_catalog import install_version, legacy_record  # noqa: E402
from app.services.problem_versions import FIELDS  # noqa: E402


async def upsert(path: Path) -> tuple[str, bool]:
    rec = json.loads(path.read_text(encoding="utf-8"))
    pid = rec["pid"]
    async with SessionLocal() as s:
        await management_lock(s)
        data = {key: rec.get(key, "") for key in FIELDS}
        data.update(statement_md=rec.get("markdown", rec.get("statement_md", "")),
                    samples=rec.get("samples") or [], tags=rec.get("tags") or [],
                    knowledge_points=rec.get("knowledge_points") or [],
                    package_level=rec.get("package_level", "C"), has_testdata=bool(rec.get("has_testdata", False)))
        _, version, _ = await install_version(s, legacy_record(data))
        created = version.revision == 1
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
            print(f"[{'new' if is_new else 'upd'}] {pid}（新版本需在管理员端核对上架）")
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
