"""导入已审阅的自建题包；--check 只检查文件，不连接数据库。"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.capabilities.test_suite import load_package  # noqa: E402


def read_packages(directory: Path) -> list[dict]:
    packages = []
    for path in sorted(directory.iterdir()):
        if not path.is_dir():
            continue
        meta, cases = load_package(path.name, directory)
        statement = (path / "statement.md").read_text(encoding="utf-8")
        sample_text = statement.split("## 样例", 1)[-1].split("## 说明", 1)[0]
        blocks = re.findall(r"```[^\n]*\n(.*?)```", sample_text, re.S)
        if len(blocks) % 2:
            raise ValueError(f"{path.name} 的公开样例输入输出不成对")
        samples = [{"input": blocks[i], "output": blocks[i + 1]}
                   for i in range(0, len(blocks), 2)]
        packages.append({"pid": path.name, "title": meta.get("title", ""),
                         "statement_md": statement, "samples": samples,
                         "difficulty": meta.get("difficulty", ""),
                         "package_level": meta["package_level"], "has_testdata": True,
                         "knowledge_points": meta.get("knowledge_points", []),
                         "source": "self-authored", "license_note": meta.get("license", ""),
                         "case_count": len(cases)})
    if not packages:
        raise ValueError("目录内没有可导入题包")
    return packages


async def import_packages(records: list[dict]) -> None:
    from sqlalchemy import select

    from app.domain.models import Problem
    from app.infra.db import SessionLocal, dispose_engine

    try:
        async with SessionLocal() as session:
            for record in records:
                row = (await session.execute(
                    select(Problem).where(Problem.pid == record["pid"])
                )).scalar_one_or_none()
                if row is None:
                    row = Problem(pid=record["pid"])
                    session.add(row)
                for key, value in record.items():
                    if key != "case_count":
                        setattr(row, key, value)
            await session.commit()
    finally:
        await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=ROOT / "problems")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        records = read_packages(args.dir)
        if not args.check:
            asyncio.run(import_packages(records))
        for record in records:
            print(f"{record['pid']}: {record['case_count']} 个正式测试点"
                  f"，{len(record['samples'])} 个公开样例")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"导入失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
