#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成金标集（150 条）：题目 × 注入 bug × 期望结论。

说明：这批是**构造集**（我们知道注入了什么 bug，标签确定），用于回归与消融；
真实学生样本后续单独统计，两者不混。

用法:
    backend\\.venv\\Scripts\\python.exe scripts/gen_golden.py
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

OUT = Path("eval/cases/golden.jsonl")

BASE = """#include <cstdio>
int main() {
    int a, b;
    scanf("%d%d", &a, &b);
    printf("%d\\n", a + b);
    return 0;
}
"""

FULLWIDTHS = ["；", "，", "（", "）", "｛", "｝", "：", "、"]


def replace_nth_semicolon(code: str, idx: int, ch: str) -> str:
    pos = -1
    for _ in range(idx + 1):
        pos = code.find(";", pos + 1)
        if pos < 0:
            return code
    return code[:pos] + ch + code[pos + 1:]


def p0_case(i: int) -> dict:
    ch = FULLWIDTHS[i % len(FULLWIDTHS)]
    slots = BASE.count(";")
    idx = i % slots
    return {
        "id": f"p0-{i:03d}",
        "pid": "P1001",
        "kind": "P0",
        "code": replace_nth_semicolon(BASE, idx, ch),
        "bug": f"第 {idx} 处分号写成全角「{ch}」",
        "expected_p": "P0",
        "expected_route": "fast_fix",
        "expect_keywords": [],
    }


P1_TEMPLATES = [
    ("missing_semicolon", """#include <cstdio>
int main() {
    int a, b;
    scanf("%d%d", &a, &b)
    printf("%d\\n", a + b);
    return 0;
}
""", "少写分号"),
    ("unbalanced_brace", """#include <cstdio>
int main() {
    int a, b;
    scanf("%d%d", &a, &b);
    printf("%d\\n", a + b);
    return 0;
""", "少一个右花括号"),
    ("undeclared", """#include <cstdio>
int main() {
    int a;
    scanf("%d", &a);
    printf("%d\\n", a + b);
    return 0;
}
""", "使用了未声明的 b"),
]


def p1_case(i: int) -> dict:
    name, code, bug = P1_TEMPLATES[i % len(P1_TEMPLATES)]
    return {
        "id": f"p1-{i:03d}-{name}",
        "pid": "P1001",
        "kind": "P1",
        "code": code,
        "bug": bug,
        "expected_p": "P1",
        "expected_route": "syntax_help",
        "expect_keywords": [],
    }


WA_VARIANTS = [
    ("wrong_operator",
     '#include <cstdio>\nint main(){int a,b;scanf("%d%d",&a,&b);printf("%d\\n",a-b);return 0;}\n',
     "把 a+b 写成 a-b", ["运算符", "减法", "a-b", "题目要求"]),
    ("wrong_operator2",
     '#include <cstdio>\nint main(){int a,b;scanf("%d%d",&a,&b);printf("%d\\n",a*b);return 0;}\n',
     "把 a+b 写成 a*b", ["乘法", "运算符", "题目要求", "a*b"]),
    ("off_by_one_sum",
     '#include <cstdio>\nint main(){int n;scanf("%d",&n);int s=0;for(int i=1;i<n;i++)s+=i;printf("%d\\n",s);return 0;}\n',
     "循环少算最后一项", ["n-1", "漏", "循环", "边界", "终止条件"]),
    ("format_prefix",
     '#include <cstdio>\nint main(){int a,b;scanf("%d%d",&a,&b);printf("sum=%d",a+b);return 0;}\n',
     "输出多了 sum= 前缀", ["格式", "前缀", "sum=", "换行"]),
    ("plus_one",
     '#include <cstdio>\nint main(){int a,b;scanf("%d%d",&a,&b);printf("%d\\n",a+b+1);return 0;}\n',
     "结果多加了 1", ["运算", "公式", "多", "1", "题目要求"]),
]


def wa_case(i: int) -> dict:
    name, code, bug, keys = WA_VARIANTS[i % len(WA_VARIANTS)]
    return {
        "id": f"wa-{i:03d}-{name}",
        "pid": "P1001",
        "kind": "WA",
        "code": code,
        "bug": bug,
        "expected_p": "P2",
        "expected_route": "llm_hypothesis",
        "expect_keywords": keys,
    }


def main() -> int:
    rows = [p0_case(i) for i in range(60)]
    rows += [p1_case(i) for i in range(30)]
    rows += [wa_case(i) for i in range(60)]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    print(f"golden: {len(rows)} 条 -> {OUT}")
    print(Counter(r["kind"] for r in rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

