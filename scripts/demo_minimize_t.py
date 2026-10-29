#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""M3 第二个演示：多组测试题（T 行）的反例最小化。

场景：阶乘题，学生用 int 存结果，n≥13 就溢出。
      1000 组数据里可能只有几组会错，ddmin 把输入缩到 1 组。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.capabilities.docker_runner import DockerRunner  # noqa: E402
from app.capabilities.minimizer import shrink_t_lines, summarize  # noqa: E402

REFERENCE = (ROOT / "problems" / "PTV-0002" / "std.cpp").read_text(encoding="utf-8")
VALIDATOR = (ROOT / "problems" / "PTV-0002" / "validator.cpp").read_text(encoding="utf-8")

STUDENT = """#include <cstdio>
int main() {
    int T; if (scanf("%d", &T) != 1) return 1;
    while (T--) {
        int n, f = 1;            // ← BUG：13! 就溢出 int
        if (scanf("%d", &n) != 1) return 1;
        for (int i = 2; i <= n; ++i) f *= i;
        printf("%d\\n", f);
    }
    return 0;
}
"""


async def main_async(t: int) -> int:
    runner = DockerRunner()
    ref = await runner.compile(REFERENCE)
    stu = await runner.compile(STUDENT)
    val = await runner.compile(VALIDATOR)
    if not (ref.ok and stu.ok and val.ok):
        print("编译失败")
        return 1

    # 1000 组里放大数，保证确实会错
    values = [str(20 if i % 7 == 0 else (i % 12) + 1) for i in range(t)]
    raw_before = f"{len(values)}\n" + "\n".join(values) + "\n"
    b_ref = await runner.run(ref.artifact_id, stdin=raw_before)
    b_stu = await runner.run(stu.artifact_id, stdin=raw_before)
    print(f"原始: T={t}, {summarize(raw_before)}")
    print(f"参考解首行={b_ref.stdout.splitlines()[:1]}  学生解首行={b_stu.stdout.splitlines()[:1]}")
    if b_ref.stdout.strip() == b_stu.stdout.strip():
        print("学生解没出错")
        return 2

    res = await shrink_t_lines(values, runner=runner,
                               student_id=stu.artifact_id, reference_id=ref.artifact_id)
    raw_after = f"{len(res.items)}\n" + "\n".join(res.items) + "\n"
    a_ref = await runner.run(ref.artifact_id, stdin=raw_after)
    a_stu = await runner.run(stu.artifact_id, stdin=raw_after)
    v_run = await runner.run(val.artifact_id, stdin=raw_after)

    print("\n=== 最小化结果 ===")
    print(f"组数: {res.original_size} → {res.size}（减少 {res.reduction:.2%}）")
    print(f"尝试次数: {res.steps}  耗时: {res.elapsed_ms} ms")
    print(f"最小反例:\n{raw_after}")
    print(f"学生输出={a_stu.stdout.strip()}  期望输出={a_ref.stdout.strip()}")
    print(f"validator: {'通过' if v_run.exit_code == 0 else '失败'}")

    report = {
        "problem": "PTV-0002",
        "original": {"T": res.original_size, **summarize(raw_before)},
        "minimized": {"T": res.size, **summarize(raw_after)},
        "steps": res.steps,
        "elapsed_ms": res.elapsed_ms,
        "reduction": round(res.reduction, 4),
        "counterexample": raw_after,
        "student_output": a_stu.stdout.strip(),
        "expected_output": a_ref.stdout.strip(),
        "validator_ok": v_run.exit_code == 0,
    }
    out = ROOT / "eval" / "reports" / "minimize_t.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告: {out}")

    for a in (ref, stu, val):
        if a.artifact_id:
            await runner.cleanup(a.artifact_id)
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--t", type=int, default=1000)
    args = ap.parse_args()
    return asyncio.run(main_async(args.t))


if __name__ == "__main__":
    raise SystemExit(main())
