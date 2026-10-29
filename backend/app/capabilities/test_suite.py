"""加载明确授权的本地题包；公开样例与正式测试始终分别标记。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.capabilities.sandbox import SandboxError

PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "problems"


@dataclass(frozen=True)
class JudgeSuite:
    scope: str
    cases: list[dict]
    digest: str
    time_limit_ms: int = 2000
    memory_limit_mb: int = 256


def load_package(pid: str, root: Path = PACKAGE_ROOT) -> tuple[dict, list[dict]]:
    """只读已审阅的 A/B 级题包，损坏或声明不一致时停止判题。"""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", pid):
        raise SandboxError("题包编号无效")
    directory = (root / pid).resolve()
    if not directory.is_relative_to(root.resolve()):
        raise SandboxError("题包路径超出配置目录")
    try:
        meta = yaml.safe_load((directory / "meta.yaml").read_text(encoding="utf-8-sig"))
        if not isinstance(meta, dict) or meta.get("id") != pid:
            raise ValueError("题包元数据编号不匹配")
        level = meta.get("package_level")
        if level not in ("A", "B") or not meta.get("reviewer"):
            raise ValueError("题包需要 A/B 等级和审核人")
        inputs = sorted((directory / "tests").glob("*.in"))
        answers = sorted((directory / "tests").glob("*.ans"))
        if {p.stem for p in inputs} != {p.stem for p in answers}:
            raise ValueError("输入与答案文件不成对")
        if len(inputs) < (3 if level == "A" else 1):
            raise ValueError("题包测试点不足")
        cases = []
        for source in inputs:
            answer = source.with_suffix(".ans")
            if any(not p.resolve().is_relative_to(directory) for p in (source, answer)):
                raise ValueError("测试文件路径超出题包")
            cases.append({
                "name": source.stem,
                "input": source.read_text(encoding="utf-8-sig"),
                "output": answer.read_text(encoding="utf-8-sig"),
            })
        for key, default, maximum in (("time_limit_ms", 2000, 30000),
                                      ("memory_limit_mb", 256, 2048)):
            value = meta.get(key, default)
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
                raise ValueError(f"{key} 无效")
        return meta, cases
    except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
        raise SandboxError(f"题包 {pid} 不可用：{exc}") from exc


def load_suite(problem) -> JudgeSuite:
    scope = "samples"
    time_limit_ms, memory_limit_mb = 2000, 256
    if problem.has_testdata and problem.package_level in ("A", "B"):
        meta, cases = load_package(problem.pid)
        if meta["package_level"] != problem.package_level:
            raise SandboxError("题包等级与入库记录不一致，请重新导入")
        scope = "package"
        time_limit_ms = meta.get("time_limit_ms", 2000)
        memory_limit_mb = meta.get("memory_limit_mb", 256)
    else:
        cases = problem.samples or []
        if not isinstance(cases, list) or any(
            not isinstance(case, dict)
            or not isinstance(case.get("input"), str)
            or not isinstance(case.get("output"), str)
            for case in cases
        ):
            raise SandboxError("题目样例缺少有效输入或标准输出，请修复题目数据")
    raw = json.dumps({
        "pid": problem.pid, "statement": problem.statement_md,
        "scope": scope, "cases": cases,
        "time_limit_ms": time_limit_ms, "memory_limit_mb": memory_limit_mb,
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return JudgeSuite(scope, cases, hashlib.sha256(raw).hexdigest(),
                      time_limit_ms, memory_limit_mb)
