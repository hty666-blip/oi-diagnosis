#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""跑金标集：提交 -> 判题 -> 诊断 -> 对标签打分。

指标：
- verdict_acc：判题结果是否符合预期（P0/P1 -> CE，WA -> WA）
- route_acc：路由是否符合预期
- p_acc：最严重 P 档位是否符合预期
- kw_hit：WA 类模型叙述是否命中人工关键词（自动打分，仅作代理指标）
- 时延与成本

用法:
    backend\\.venv\\Scripts\\python.exe scripts\\run_golden_eval.py --limit 20
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000/api/v1"


def load(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="eval/cases/golden.jsonl")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="eval/reports/golden_v1.json")
    ap.add_argument("--kind", default="", help="只看某一类：P0/P1/WA")
    args = ap.parse_args(argv)

    tok = httpx.post(f"{BASE}/auth/login",
                     json={"username": "student01", "password": "123456"},
                     timeout=20).json()["access_token"]
    H = {"Authorization": f"Bearer {tok}"}

    cases = load(Path(args.cases))
    if args.kind:
        cases = [c for c in cases if c["kind"] == args.kind]
    if args.limit:
        cases = cases[: args.limit]

    rows = []
    t0 = time.time()
    for i, c in enumerate(cases, 1):
        try:
            t_sub = time.time()
            sub = httpx.post(f"{BASE}/submissions",
                             json={"problem_pid": c["pid"], "code": c["code"]}, headers=H,
                             timeout=180).json()
            t1 = time.time()
            g = httpx.post(f"{BASE}/submissions/{sub['id']}/diagnose", headers=H, timeout=180).json()
            latency = int((time.time() - t1) * 1000)
            submit_ms = int((t1 - t_sub) * 1000)
        except Exception as e:  # noqa: BLE001
            rows.append({"id": c["id"], "error": f"{type(e).__name__}: {e}"})
            print(f"[{i}/{len(cases)}] {c['id']} ERROR")
            continue

        nature = g.get("nature") or []
        p_pred = nature[0]["p"] if nature else None
        narrative = g.get("narrative") or ""
        kw = c.get("expect_keywords") or []
        kw_hit = any(k.lower() in narrative.lower() for k in kw) if kw else None

        rows.append({
            "id": c["id"], "kind": c["kind"], "bug": c["bug"],
            "verdict": sub["verdict"], "status": g["status"], "route": g["route"],
            "confidence": g["confidence"], "p_pred": p_pred,
            "narrative": narrative[:200], "kw_hit": kw_hit, "latency_ms": latency, "submit_ms": submit_ms,
        })
        flag = "OK" if (c["expected_route"] == g["route"] or
                        (c["kind"] == "WA" and g["route"] in ("llm_hypothesis", "full_pipeline", "human"))) else "MISS"
        print(f"[{i}/{len(cases)}] {c['id']:<28} {sub['verdict']:<3} route={g['route']:<15} "
              f"p={p_pred or '-':<3} {flag} {latency}ms")

    ok = [r for r in rows if "error" not in r]
    def rate(key, fn):
        vals = [fn(r) for r in ok]
        return round(sum(1 for v in vals if v) / len(vals), 4) if vals else 0.0

    verdict_acc = rate("v", lambda r: (
        r["verdict"] == "CE" if r["kind"] in ("P0", "P1") else r["verdict"] == "WA"))
    route_acc = rate("route", lambda r: r["route"] == next(
        c["expected_route"] for c in cases if c["id"] == r["id"]))
    p0_rows = [r for r in ok if r["kind"] == "P0"]
    p1_rows = [r for r in ok if r["kind"] == "P1"]
    p0_acc = round(sum(1 for r in p0_rows if r["p_pred"] == "P0") / len(p0_rows), 4) if p0_rows else None
    p1_acc = round(sum(1 for r in p1_rows if r["p_pred"] == "P1") / len(p1_rows), 4) if p1_rows else None
    kw_rows = [r for r in ok if r["kw_hit"] is not None]
    kw_acc = round(sum(1 for r in kw_rows if r["kw_hit"]) / len(kw_rows), 4) if kw_rows else None

    report = {
        "total": len(rows),
        "ok": len(ok),
        "verdict_acc": verdict_acc,
        "route_acc": route_acc,
        "p_level_acc": {"P0": p0_acc, "P1": p1_acc},
        "wa_keyword_hit": kw_acc,
        "latency_ms": {
            "mean": int(statistics.mean(r["latency_ms"] for r in ok)) if ok else 0,
            "p95": int(sorted(r["latency_ms"] for r in ok)[int(len(ok) * 0.95) - 1]) if ok else 0,
            "submit_mean": int(statistics.mean(r["submit_ms"] for r in ok)) if ok else 0,
        },
        "elapsed_s": int(time.time() - t0),
        "by_kind": {},
        "rows": rows,
    }
    for kind in ("P0", "P1", "WA"):
        sub = [r for r in ok if r["kind"] == kind]
        if sub:
            report["by_kind"][kind] = {
                "n": len(sub),
                "verdict_acc": round(sum(1 for r in sub if (
                    r["verdict"] == "CE" if kind in ("P0", "P1") else r["verdict"] == "WA")) / len(sub), 4),
                "route": {r["route"]: sum(1 for x in sub if x["route"] == r["route"])
                          for r in sub},
            }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 汇总 ===")
    print(f"样本 {report['ok']}/{report['total']}  用时 {report['elapsed_s']}s")
    print(f"verdict 准确率 : {verdict_acc:.1%}")
    print(f"route 准确率   : {route_acc:.1%}")
    print(f"P0 档位准确率  : {p0_acc:.1%}" if p0_acc is not None else "P0: n/a")
    print(f"P1 档位准确率  : {p1_acc:.1%}" if p1_acc is not None else "P1: n/a")
    print(f"WA 关键词命中  : {kw_acc:.1%}" if kw_acc is not None else "WA: n/a")
    print(f"时延 mean/p95  : {report['latency_ms']['mean']} / {report['latency_ms']['p95']} ms")
    print(f"报告: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())



