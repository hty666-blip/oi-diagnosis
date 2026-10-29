#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""go-judge 冒烟测试：版本 + 运行 + 编译。

用法:
    backend\\.venv\\Scripts\\python.exe scripts\\judge_smoke.py
"""
from __future__ import annotations

import base64
import json
import sys

import httpx

JUDGE = "http://localhost:5050"
CPP = """#include <cstdio>
int main() { int a, b; if (scanf("%d%d", &a, &b) != 2) return 1; printf("%d\\n", a + b); return 0; }
"""


def run(payload: dict) -> dict:
    r = httpx.post(f"{JUDGE}/run", json=payload, timeout=60)
    r.raise_for_status()
    return r.json()


def first(res) -> dict:
    """/run 直接返回结果数组；兼容包了 results 字段的版本。"""
    if isinstance(res, list):
        return res[0]
    return res["results"][0]


def decode(files: dict | None) -> dict:
    out = {}
    for k, v in (files or {}).items():
        try:
            out[k] = base64.b64decode(v).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            out[k] = str(v)[:200]
    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    print("1) /version")
    v = httpx.get(f"{JUDGE}/version", timeout=10).json()
    print("   ", json.dumps(v, ensure_ascii=False)[:160])

    print("2) 运行 /bin/echo")
    res = run({
        "cmd": [{"args": ["/bin/echo", "hello-judge"], "env": ["PATH=/usr/bin:/bin"]}],
        "timeLimit": 3_000_000_000,
        "memoryLimit": 256 * 1024 * 1024,
        "procLimit": 1,
        "copyIn": {},
        "copyOut": ["stdout", "stderr"],
    })
    r0 = first(res)
    print("   status=", r0.get("status"), "exit=", r0.get("exitStatus"), "time(ns)=", r0.get("time"))
    print("   files=", decode(r0.get("files")))

    print("3) 编译 main.cpp")
    res = run({
        "cmd": [{
            "args": ["/usr/bin/g++", "-O2", "-std=c++17", "-o", "a.out", "main.cpp"],
            "env": ["PATH=/usr/bin:/bin"],
            "cpuLimit": 10_000_000_000,
            "memoryLimit": 512 * 1024 * 1024,
            "procLimit": 64,
        }],
        "timeLimit": 15_000_000_000,
        "memoryLimit": 512 * 1024 * 1024,
        "procLimit": 64,
        "copyIn": {"main.cpp": {"content": CPP}},
        "copyOut": ["stdout", "stderr"],
    })
    r0 = first(res)
    files = decode(r0.get("files"))
    print("   status=", r0.get("status"), "exit=", r0.get("exitStatus"))
    print("   stderr=", (files.get("stderr") or "")[:300])
    return 0 if r0.get("exitStatus") == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

