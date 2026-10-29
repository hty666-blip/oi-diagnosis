#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成 Triage 评测集（fixtures）。

三类样本：
  1. P0 表层：全角/中文标点（确定性可判，不需要编译器）
  2. P1 语法：模板代码，用 DockerRunner 真实编译拿 stderr
  3. 干净样本：能编译，应走 full_pipeline

用法:
    backend\\.venv\\Scripts\\python.exe scripts\\gen_triage_fixtures.py --out eval/fixtures/triage.jsonl
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

BASE = """#include <cstdio>
int main() {{
    int a, b;
    scanf("%d%d", &a, &b)@@
    printf("%d\\n", a + b)@@
    return 0@@
}}
"""

FULLWIDTHS = ["；", "，", "（", "）", "｛", "｝", "：", "、"]


def make_p0(ch: str, variant: int) -> str:
    slots = BASE.count("@@")
    idx = variant % slots
    code = BASE
    for k in range(slots):
        code = code.replace("@@", ch if k == idx else ";", 1)
    pad = " " * (variant // slots)
    return code.replace("    int a, b;", f"    int a, b;{pad}")


P1_TEMPLATES = {
    "missing_semicolon": """#include <cstdio>
int main() {
    int a = 1
    printf("%d\\n", a);
    return 0;
}
""",
    "unbalanced_brace": """#include <cstdio>
int main() {
    int a = 1;
    printf("%d\\n", a);
    return 0;
""",
    "undeclared": """#include <cstdio>
int main() {
    int a = 1;
    printf("%d\\n", b);
    return 0;
}
""",
    "missing_include": """int main() {
    cout << 1 << endl;
    return 0;
}
""",
    "no_main": """#include <cstdio>
int mian() {
    return 0;
}
""",
    "redefinition": """#include <cstdio>
int main() {
    int a = 1;
    int a = 2;
    printf("%d\\n", a);
    return 0;
}
""",
}

CLEAN = [
    """#include <cstdio>
int main(){int a,b;scanf("%d%d",&a,&b);printf("%d\\n",a+b);return 0;}
""",
    """#include <cstdio>
int main(){int n,s=0;scanf("%d",&n);for(int i=1;i<=n;i++)s+=i;printf("%d\\n",s);return 0;}
""",
    """#include <cstdio>
int main(){printf("hello\\n");return 0;}
""",
]


async def build(out: Path) -> int:
    rows: list[dict] = []

    # 1) P0：8 种全角字符 × 20 变体 = 160
    for ch_i, ch in enumerate(FULLWIDTHS):
        for v in range(20):
            rows.append({
                "id": f"p0-{ch_i}-{v:02d}",
                "kind": "P0",
                "code": make_p0(ch, v),
                "expected_p": "P0",
                "expected_route": "fast_fix",
                "stderr": None,
                "verdict": "CE",
            })

    # 2) P1：真实编译拿 stderr（6 条）
    runner = DockerRunner()
    for name, code in P1_TEMPLATES.items():
        comp = await runner.compile(code)
        rows.append({
            "id": f"p1-{name}",
            "kind": "P1",
            "code": code,
            "expected_p": "P1",
            "expected_route": "syntax_help",
            "stderr": comp.stderr,
            "verdict": "CE",
            "compiled_ok": comp.ok,
        })
        if comp.artifact_id:
            await runner.cleanup(comp.artifact_id)

    # 3) 干净样本（34 条，凑满 200）
    for i in range(34):
        code = CLEAN[i % len(CLEAN)]
        rows.append({
            "id": f"clean-{i:02d}",
            "kind": "clean",
            "code": code,
            "expected_p": None,
            "expected_route": "full_pipeline",
            "stderr": None,
            "verdict": "WA",
        })

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    print(f"fixtures: {len(rows)} 条 -> {out.resolve()}")
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="eval/fixtures/triage.jsonl")
    args = ap.parse_args()
    return asyncio.run(build(Path(args.out)))


if __name__ == "__main__":
    raise SystemExit(main())
