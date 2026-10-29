#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Triage 分诊评测：跑 fixtures，算 P 档位准确率与路由准确率。

用法:
    backend\\.venv\\Scripts\\python.exe scripts\\run_triage_eval.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.capabilities.triage import triage  # noqa: E402


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", default="eval/fixtures/triage.jsonl")
    ap.add_argument("--out", default="eval/reports/triage.json")
    args = ap.parse_args(argv)

    rows = load(Path(args.fixtures))
    t0 = time.time()

    p_ok = p_bad = route_ok = route_bad = 0
    by_kind: dict[str, Counter] = {}
    failures: list[dict] = []

    for r in rows:
        tr = triage(r["code"], compile_stderr=r.get("stderr"), verdict=r.get("verdict"))
        pred_p = tr.worst_p
        exp_p = r.get("expected_p")
        pred_route = tr.route
        exp_route = r.get("expected_route")

        kind = r["kind"]
        by_kind.setdefault(kind, Counter())["n"] += 1

        if pred_p == exp_p:
            p_ok += 1
            by_kind[kind]["p_ok"] += 1
        else:
            p_bad += 1
            failures.append({"id": r["id"], "kind": kind, "field": "p",
                             "expected": exp_p, "predicted": pred_p,
                             "codes": [f.code for f in tr.findings][:3]})

        if pred_route == exp_route:
            route_ok += 1
            by_kind[kind]["route_ok"] += 1
        else:
            route_bad += 1
            failures.append({"id": r["id"], "kind": kind, "field": "route",
                             "expected": exp_route, "predicted": pred_route})

    total = len(rows)
    report = {
        "total": total,
        "p_accuracy": round(p_ok / total, 4),
        "route_accuracy": round(route_ok / total, 4),
        "elapsed_ms": int((time.time() - t0) * 1000),
        "by_kind": {k: dict(v) for k, v in by_kind.items()},
        "failures": failures[:20],
    }

    print(f"\n样本 {total} 条，耗时 {report['elapsed_ms']} ms")
    print(f"P 档位准确率   : {report['p_accuracy']:.1%}  ({p_ok}/{total})")
    print(f"路由准确率     : {report['route_accuracy']:.1%}  ({route_ok}/{total})")
    print("\n分类明细:")
    for k, v in report["by_kind"].items():
        n = v["n"]
        print(f"  {k:6s} n={n:3d}  P准确={v.get('p_ok',0)/n:.0%}  路由准确={v.get('route_ok',0)/n:.0%}")

    if failures:
        print(f"\n前 8 条失败:")
        for f in failures[:8]:
            print(f"  [{f['id']}] {f['field']}: 期望 {f['expected']} 实际 {f['predicted']} {f.get('codes','')}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告: {out}")
    return 0 if report["p_accuracy"] >= 0.95 else 1


if __name__ == "__main__":
    raise SystemExit(main())
