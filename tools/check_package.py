#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""题包结构校验器（不依赖编译器 / 不联网）

用途:
    在题库入库前做"结构 + 元数据"检查；真正的编译、validator、对拍
    由 go-judge 在容器里跑（见架构文档 §9.6 与题库自建方案 §3）。

用法:
    python tools/check_package.py --dir problems
    python tools/check_package.py --dir problems --json report.json
    python tools/check_package.py --dir problems/P1001-ovf

退出码: 0 = 全部通过；1 = 存在 error；2 = 只有 warning（需要人工确认）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

VALID_ERRORS = {f"M{i:02d}" for i in range(1, 12)}
VALID_DIFFICULTY = {"入门", "普及−", "普及/提高−", "普及+/提高", "提高+/省选−", "省选/NOI−", "NOI/NOI+/CTSC"}
VALID_LEVELS = {"A", "B", "C", "D"}
REQUIRED_FIELDS = ["id", "title", "difficulty", "package_level", "target_errors", "license"]
LEVEL_MIN_TESTS = {"A": 3, "B": 1, "C": 0, "D": 0}
LEVEL_FILES = {
    "A": ["validator.cpp", "std.cpp", "brute.cpp", "gen.py"],
    "B": ["validator.cpp", "std.cpp", "gen.py"],
    "C": [],
    "D": [],
}


@dataclass
class Result:
    path: Path
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def load_meta(path: Path) -> dict:
    """优先 PyYAML；缺失时用极简解析（只取顶层标量与列表）。"""
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text) or {}
    except Exception:  # noqa: BLE001
        pass

    data: dict = {}
    key = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith((" ", "\t")) and ":" in line:
            k, v = line.split(":", 1)
            key, v = k.strip(), v.strip()
            if v.startswith("[") and v.endswith("]"):
                data[key] = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
            elif v:
                data[key] = v.strip("'\"")
            else:
                data[key] = []
            continue
        s = line.strip()
        if key and s.startswith("- "):
            if not isinstance(data.get(key), list):
                data[key] = []
            data[key].append(s[2:].strip().strip("'\""))
    return data


def check_one(pkg: Path, all_ids: dict[str, Path]) -> Result:
    r = Result(path=pkg)

    meta_path = pkg / "meta.yaml"
    if not meta_path.exists():
        meta_path = pkg / "meta.json"
    if not meta_path.exists():
        r.errors.append("缺少 meta.yaml / meta.json")
        return r

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.suffix == ".json" else load_meta(meta_path)
    except Exception as e:  # noqa: BLE001
        r.errors.append(f"meta 解析失败: {type(e).__name__}: {e}")
        return r
    r.meta = meta

    for f in REQUIRED_FIELDS:
        if not meta.get(f):
            r.errors.append(f"meta 缺字段: {f}")

    # id 唯一
    pid = str(meta.get("id") or "")
    if pid:
        if pid in all_ids and all_ids[pid] != pkg:
            r.errors.append(f"id 重复: {pid} 也出现在 {all_ids[pid]}")
        else:
            all_ids[pid] = pkg

    # 枚举校验
    level = str(meta.get("package_level") or "").upper()
    if level and level not in VALID_LEVELS:
        r.errors.append(f"package_level 非法: {level}（应为 A/B/C/D）")

    diff = str(meta.get("difficulty") or "")
    if diff and diff not in VALID_DIFFICULTY:
        r.warnings.append(f"difficulty 不在标准档位: {diff}")

    errs = meta.get("target_errors") or []
    if isinstance(errs, str):
        errs = [errs]
    bad = [e for e in errs if e not in VALID_ERRORS]
    if bad:
        r.errors.append(f"target_errors 含非法编码: {bad}（应为 M01~M11）")
    if not errs:
        r.warnings.append("target_errors 为空：这道题不是错因探针")

    # 文件完整性
    for fn in LEVEL_FILES.get(level, []):
        if not (pkg / fn).exists():
            r.errors.append(f"{level} 级题包缺少文件: {fn}")

    # 题面
    stmt = pkg / "statement.md"
    if not stmt.exists():
        r.errors.append("缺少 statement.md")
    else:
        s = stmt.read_text(encoding="utf-8")
        if len(s) < 80:
            r.warnings.append("statement.md 过短（<80 字符）")
        if "样例" not in s and "```" not in s:
            r.warnings.append("statement.md 里没找到样例代码块")
        for sec in ("输入格式", "输出格式"):
            if sec not in s:
                r.warnings.append(f"statement.md 缺少「{sec}」小节")

    # 测试点
    tdir = pkg / "tests"
    ins = sorted(tdir.glob("*.in")) if tdir.exists() else []
    ans = sorted(tdir.glob("*.ans")) if tdir.exists() else []
    if not tdir.exists():
        if level in ("A", "B"):
            r.errors.append("A/B 级题包缺少 tests/ 目录")
    else:
        in_stems = {p.stem for p in ins}
        ans_stems = {p.stem for p in ans}
        if in_stems != ans_stems:
            r.errors.append(f"测试点不成对: 只有 in={sorted(in_stems - ans_stems)} 只有 ans={sorted(ans_stems - in_stems)}")
        need = LEVEL_MIN_TESTS.get(level, 0)
        if len(ins) < need:
            r.errors.append(f"{level} 级至少 {need} 组测试点，当前 {len(ins)} 组")
        for p in ins:
            if p.stat().st_size == 0:
                r.errors.append(f"空测试点: {p.name}")

    # AI 生成必须带验证记录
    if str(meta.get("ai_generated")).lower() in ("true", "yes", "1"):
        raw = meta_path.read_text(encoding="utf-8")
        if "verified" not in raw:
            r.errors.append("ai_generated=true 但缺少 verified 验证记录块")
        if not meta.get("reviewer") and "reviewer" not in raw:
            r.warnings.append("AI 生成的题缺少 reviewer（人审）")

    r.stats = {
        "level": level,
        "tests": len(ins),
        "target_errors": errs,
        "knowledge_points": meta.get("knowledge_points") or [],
    }
    return r


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    ap = argparse.ArgumentParser(description="题包结构校验器")
    ap.add_argument("--dir", required=True, help="题包根目录，或单个题包目录")
    ap.add_argument("--json", help="把报告写成 JSON")
    args = ap.parse_args(argv)

    root = Path(args.dir)
    if not root.exists():
        print(f"目录不存在: {root}")
        return 1
    pkgs = [root] if (root / "meta.yaml").exists() or (root / "meta.json").exists() else \
           [p for p in sorted(root.iterdir()) if p.is_dir()]

    all_ids: dict[str, Path] = {}
    results = [check_one(p, all_ids) for p in pkgs]

    print(f"\n校验 {len(results)} 个题包 @ {root.resolve()}\n")
    print(f"{'题包':<28}{'级别':<6}{'测试点':<8}{'错因探针':<24}状态")
    print("-" * 88)
    for r in results:
        st = r.stats
        state = "OK" if r.ok and not r.warnings else ("WARN" if r.ok else "ERROR")
        print(f"{r.path.name:<28}{st.get('level','?'):<6}{st.get('tests',0):<8}"
              f"{','.join(st.get('target_errors') or ['-']):<24}{state}")
    for r in results:
        for e in r.errors:
            print(f"  [ERROR] {r.path.name}: {e}")
        for w in r.warnings:
            print(f"  [WARN ] {r.path.name}: {w}")

    # 探针覆盖
    covered = {e for r in results for e in (r.stats.get("target_errors") or [])}
    missing = sorted(VALID_ERRORS - covered)
    print(f"\n错因覆盖: {len(covered)}/11  未覆盖: {missing or '无'}")

    if args.json:
        Path(args.json).write_text(json.dumps(
            [{"path": str(r.path), "ok": r.ok, "errors": r.errors,
              "warnings": r.warnings, "stats": r.stats} for r in results],
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告已写入: {args.json}")

    if any(r.errors for r in results):
        return 1
    return 2 if any(r.warnings for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
