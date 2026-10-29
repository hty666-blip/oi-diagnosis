#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""M3 批量评估：多种错解 × 两种输入结构，统计最小化效果。

输出: eval/reports/minimize_matrix.json
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.capabilities.docker_runner import DockerRunner  # noqa: E402
from app.capabilities.minimizer import (  # noqa: E402
    render_array, shrink_array_sum, shrink_t_lines, summarize,
)


def p(pid: str, name: str) -> str:
    return (ROOT / "problems" / pid / name).read_text(encoding="utf-8")


STD_1 = p("PTV-0001", "std.cpp")
STD_2 = p("PTV-0002", "std.cpp")
VAL_1 = p("PTV-0001", "validator.cpp")
VAL_2 = p("PTV-0002", "validator.cpp")

BUGS = {
    # 数组求和：int 溢出 / 少加一个 / 初值错
    "sum_int": """#include <cstdio>
int main(){int n;if(scanf("%d",&n)!=1)return 1;int s=0;for(int i=0;i<n;i++){int x;if(scanf("%d",&x)!=1)return 1;s+=x;}printf("%d\\n",s);return 0;}
""",
    "sum_off_by_one": """#include <cstdio>
int main(){int n;if(scanf("%d",&n)!=1)return 1;long long s=0;for(int i=0;i<n-1;i++){long long x;if(scanf("%lld",&x)!=1)return 1;s+=x;}printf("%lld\\n",s);return 0;}
""",
    "sum_init_one": """#include <cstdio>
int main(){int n;if(scanf("%d",&n)!=1)return 1;long long s=1;for(int i=0;i<n;i++){long long x;if(scanf("%lld",&x)!=1)return 1;s+=x;}printf("%lld\\n",s);return 0;}
""",
    # 阶乘：int 溢出 / 循环少一轮
    "fact_int": """#include <cstdio>
int main(){int T;if(scanf("%d",&T)!=1)return 1;while(T--){int n,f=1;if(scanf("%d",&n)!=1)return 1;for(int i=2;i<=n;i++)f*=i;printf("%d\\n",f);}return 0;}
""",
    "fact_short_loop": """#include <cstdio>
int main(){int T;if(scanf("%d",&T)!=1)return 1;while(T--){int n;long long f=1;if(scanf("%d",&n)!=1)return 1;for(int i=2;i<n;i++)f*=i;printf("%lld\\n",f);}return 0;}
""",
}


async def run_case(runner, name: str, code: str, kind: str) -> dict:
    ref_src, val_src = (STD_1, VAL_1) if kind == "sum" else (STD_2, VAL_2)
    ref = await runner.compile(ref_src)
    stu = await runner.compile(code)
    val = await runner.compile(val_src)
    if not (ref.ok and stu.ok and val.ok):
        return {"case": name, "ok": False, "error": "compile failed"}

    if kind == "sum":
        values = ["1000000000"] * 3000
        before = render_array(values)
        res = await shrink_array_sum(values, runner=runner,
                                     student_id=stu.artifact_id, reference_id=ref.artifact_id)
        after = render_array(res.items)
    else:
        values = [str(20 if i % 5 == 0 else (i % 12) + 1) for i in range(600)]
        before = f"{len(values)}\n" + "\n".join(values) + "\n"
        res = await shrink_t_lines(values, runner=runner,
                                   student_id=stu.artifact_id, reference_id=ref.artifact_id)
        after = f"{len(res.items)}\n" + "\n".join(res.items) + "\n"

    v = await runner.run(val.artifact_id, stdin=after)
    r2 = await runner.run(ref.artifact_id, stdin=after)
    s2 = await runner.run(stu.artifact_id, stdin=after)
    reproduced = (r2.stdout.strip() != s2.stdout.strip())

    for a in (ref, stu, val):
        if a.artifact_id:
            await runner.cleanup(a.artifact_id)

    return {
        "case": name, "ok": True,
        "before_n": res.original_size, "after_n": res.size,
        "before": summarize(before), "after": summarize(after),
        "steps": res.steps, "elapsed_ms": res.elapsed_ms,
        "reduction": round(res.reduction, 4),
        "validator_ok": v.exit_code == 0,
        "reproduced": reproduced,
        "counterexample": after,
    }


async def main() -> int:
    runner = DockerRunner()
    cases = [
        ("sum_int", BUGS["sum_int"], "sum"),
        ("sum_off_by_one", BUGS["sum_off_by_one"], "sum"),
        ("sum_init_one", BUGS["sum_init_one"], "sum"),
        ("fact_int", BUGS["fact_int"], "fact"),
        ("fact_short_loop", BUGS["fact_short_loop"], "fact"),
    ]
    results = []
    for name, code, kind in cases:
        r = await run_case(runner, name, code, kind)
        results.append(r)
        if r.get("ok"):
            print(f"[{name}] {r['before_n']} → {r['after_n']} 步={r['steps']} "
                  f"耗时={r['elapsed_ms']}ms 合法={r['validator_ok']} 复现={r['reproduced']}")
        else:
            print(f"[{name}] 失败: {r.get('error')}")

    ok = [r for r in results if r.get("ok")]
    stats = {
        "cases": len(results), "succeeded": len(ok),
        "success_rate": round(len(ok) / len(results), 4),
        "avg_after_lines": round(sum(r["after"]["lines"] for r in ok) / len(ok), 2) if ok else 0,
        "avg_reduction": round(sum(r["reduction"] for r in ok) / len(ok), 4) if ok else 0,
        "validator_pass_rate": round(sum(1 for r in ok if r["validator_ok"]) / len(ok), 4) if ok else 0,
        "reproduce_rate": round(sum(1 for r in ok if r["reproduced"]) / len(ok), 4) if ok else 0,
        "avg_steps": round(sum(r["steps"] for r in ok) / len(ok), 1) if ok else 0,
    }
    report = {"stats": stats, "results": results}
    out = ROOT / "eval" / "reports" / "minimize_matrix.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 汇总 ===")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"报告: {out}")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(asyncio.run(main()))
