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
from app.services.problem_catalog import local_package  # noqa: E402


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
        packages[-1]["catalog_record"] = local_package(path.name, directory).record()
    if not packages:
        raise ValueError("目录内没有可导入题包")
    if len({p["pid"].upper() for p in packages}) != len(packages):
        raise ValueError("题包编号重复，大小写不能区分同一道题")
    return packages


async def import_packages(records: list[dict], *, publish: bool = False) -> None:
    from app.infra.db import SessionLocal, dispose_engine
    from app.services.admin_service import management_lock
    from app.services.problem_catalog import install_version

    try:
        async with SessionLocal() as session:
            await management_lock(session)
            for record in records:
                row, _, changed = await install_version(session, record["catalog_record"])
                if publish and changed:
                    row.is_active = True
                    row.catalog_version += 1
            await session.commit()
    finally:
        await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=ROOT / "problems")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--publish", action="store_true", help="明确上架本次新导入且已审阅的自建版本，不恢复已下架的相同版本")
    args = parser.parse_args()
    try:
        records = read_packages(args.dir)
        if not args.check:
            asyncio.run(import_packages(records, publish=args.publish))
        for record in records:
            print(f"{record['pid']}: {record['case_count']} 个正式测试点"
                  f"，{len(record['samples'])} 个公开样例")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"导入失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
