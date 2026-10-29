#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""M3 演示：把"十万行才出错"的输入缩到能手算的大小。

场景：求和题的参考解用 long long，学生用 int 累加。
      大输入下 int 溢出 → 答案错；ddmin 逐步删元素，直到再删就不会错。

用法:
    backend\\.venv\\Scripts\\python.exe scripts\\demo_minimize.py --n 5000
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
from app.capabilities.minimizer import render_array, shrink_array_sum, summarize  # noqa: E402

REFERENCE = (ROOT / "problems" / "PTV-0001" / "std.cpp").read_text(encoding="utf-8")

STUDENT = """#include <cstdio>
int main() {
    int n; if (scanf("%d", &n) != 1) return 1;
    int sum = 0;                 // ← BUG：int 装不下 1e14
    for (int i = 0; i < n; ++i) {
        int x; if (scanf("%d", &x) != 1) return 1;
        sum += x;
    }
    printf("%d\\n", sum);
    return 0;
}
"""


async def main_async(n: int) -> int:
    runner = DockerRunner()
    print("编译参考解 / 学生解 ...")
    ref = await runner.compile(REFERENCE)
    stu = await runner.compile(STUDENT)
    if not ref.ok or not stu.ok:
        print("编译失败:", ref.stderr[:300], stu.stderr[:300])
        return 1

    values = ["1000000000"] * n
    raw_before = render_array(values)
    print(f"原始输入: n={n}, {summarize(raw_before)}")

    # 先确认它确实会错
    before_ref = await runner.run(ref.artifact_id, stdin=raw_before, time_limit_ms=5000)
    before_stu = await runner.run(stu.artifact_id, stdin=raw_before, time_limit_ms=5000)
    print(f"参考解输出={before_ref.stdout.strip()[:30]}  学生解输出={before_stu.stdout.strip()[:30]}")
    if before_stu.stdout.strip() == before_ref.stdout.strip():
        print("学生解没出错，无法演示最小化")
        return 2

    res = await shrink_array_sum(
        values, runner=runner, student_id=stu.artifact_id,
        reference_id=ref.artifact_id, max_steps=80,
    )
    raw_after = render_array(res.items)
    after_stu = await runner.run(stu.artifact_id, stdin=raw_after, time_limit_ms=5000)
    after_ref = await runner.run(ref.artifact_id, stdin=raw_after, time_limit_ms=5000)

    # 用题目自带的 validator 校验最小反例的合法性（格式/范围）
    val_src = (ROOT / "problems" / "PTV-0001" / "validator.cpp").read_text(encoding="utf-8")
    val_comp = await runner.compile(val_src)
    validator_ok = False
    val_msg = "未编译"
    if val_comp.ok and val_comp.artifact_id:
        val_run = await runner.run(val_comp.artifact_id, stdin=raw_after, time_limit_ms=2000)
        validator_ok = val_run.exit_code == 0
        val_msg = (val_run.stdout + val_run.stderr).strip()[:80] or "OK"

    print("\n=== 最小化结果 ===")
    print(f"元素: {res.original_size} → {res.size}（减少 {res.reduction:.2%}）")
    print(f"尝试次数: {res.steps}  耗时: {res.elapsed_ms} ms")
    print(f"最小反例:\n{raw_after}")
    print(f"复现校验: 学生={after_stu.stdout.strip()} 期望={after_ref.stdout.strip()}")
    print(f"合法性校验(validator): {'通过' if validator_ok else '失败'}  {val_msg}")

    report = {
        "problem": "PTV-0001",
        "original": {"n": res.original_size, **summarize(raw_before)},
        "minimized": {"n": res.size, **summarize(raw_after)},
        "steps": res.steps,
        "elapsed_ms": res.elapsed_ms,
        "reduction": round(res.reduction, 4),
        "counterexample": raw_after,
        "student_output": after_stu.stdout.strip(),
        "expected_output": after_ref.stdout.strip(),
        "validator_ok": validator_ok,
    }
    out = ROOT / "eval" / "reports" / "minimize.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告: {out}")

    await runner.cleanup(ref.artifact_id)
    await runner.cleanup(stu.artifact_id)
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5000)
    args = ap.parse_args()
    return asyncio.run(main_async(args.n))


if __name__ == "__main__":
    raise SystemExit(main())
