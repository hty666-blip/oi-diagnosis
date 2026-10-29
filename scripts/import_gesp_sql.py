#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 Navicat 导出的 MySQL `problems` dump 转成我们的题面 JSON。

用法:
    backend\\.venv\\Scripts\\python.exe scripts/import_gesp_sql.py ^
        --sql "D:\\...\\problems.sql" --out data/gesp
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DIFFICULTY = {
    0: "暂无评定", 1: "入门", 2: "普及−", 3: "普及/提高−",
    4: "普及+/提高", 5: "提高+/省选−", 6: "省选/NOI−", 7: "NOI/NOI+/CTSC",
}


def parse_columns(sql: str) -> list[str]:
    m = re.search(r"CREATE TABLE `problems`\s*\((.*?)\n\)\s*ENGINE", sql, re.S)
    if not m:
        raise ValueError("找不到 CREATE TABLE problems")
    cols = re.findall(r"^\s*`(\w+)`\s+", m.group(1), re.M)
    return cols


def split_tuples(values: str) -> list[str]:
    """按顶层括号切出每个 (...)；跳过字符串内的括号。"""
    out, depth, start, in_str, esc = [], 0, None, False, False
    for i, ch in enumerate(values):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == "'":
                # '' 视为转义的单引号
                if i + 1 < len(values) and values[i + 1] == "'":
                    pass
                else:
                    in_str = False
            continue
        if ch == "'":
            in_str = True
        elif ch == "(":
            if depth == 0:
                start = i + 1
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0 and start is not None:
                out.append(values[start:i])
                start = None
    return out


def split_fields(row: str) -> list[str | None]:
    """切一行里顶层逗号分隔的字段，返回原始 token（去掉两侧空白）。"""
    fields, cur, depth, in_str, esc = [], [], 0, False, False
    i = 0
    while i < len(row):
        ch = row[i]
        if in_str:
            cur.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == "'":
                if i + 1 < len(row) and row[i + 1] == "'":
                    cur.append("'")
                    i += 2
                    continue
                in_str = False
        else:
            if ch == "'":
                in_str = True
                cur.append(ch)
            elif ch == "(":
                depth += 1
                cur.append(ch)
            elif ch == ")":
                depth -= 1
                cur.append(ch)
            elif ch == "," and depth == 0:
                fields.append("".join(cur).strip())
                cur = []
            else:
                cur.append(ch)
        i += 1
    fields.append("".join(cur).strip())
    return fields


UNESCAPE = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", "'": "'", '"': '"', "Z": "\x1a"}


def decode(token: str) -> str | None:
    if token.upper() == "NULL":
        return None
    if not (token.startswith("'") and token.endswith("'")):
        return token
    body = token[1:-1]
    out, i = [], 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            out.append(UNESCAPE.get(nxt, nxt))
            i += 2
        elif ch == "'" and i + 1 < len(body) and body[i + 1] == "'":
            out.append("'")
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def build_statement(code: str, title: str, bg: str | None, desc: str | None,
                    fin: str | None, fout: str | None, hint: str | None) -> str:
    parts = [f"# {code} {title}".strip()]
    for name, body in (("题目背景", bg), ("题目描述", desc),
                       ("输入格式", fin), ("输出格式", fout), ("说明/提示", hint)):
        if body and body.strip():
            parts.append(f"## {name}\n\n{body.strip()}")
    return "\n\n".join(parts) + "\n"


def to_record(cols: list[str], vals: list[str | None], tag_map: dict[str, str]) -> dict:
    d = dict(zip(cols, vals))
    code = d.get("problem_code") or ""
    title = d.get("title") or ""

    samples = []
    try:
        raw = json.loads(d.get("samples") or "[]")
        for item in raw:
            if isinstance(item, dict):
                samples.append({"input": item.get("input", ""), "output": item.get("output", "")})
    except Exception:  # noqa: BLE001
        pass

    tags = []
    try:
        ids = json.loads(d.get("tags") or "[]")
        tags = [tag_map.get(str(t), f"tag:{t}") for t in ids]
    except Exception:  # noqa: BLE001
        pass

    diff_id = d.get("difficulty")
    try:
        diff = DIFFICULTY.get(int(diff_id), "未知") if diff_id is not None else "未知"
    except Exception:  # noqa: BLE001
        diff = "未知"

    md = build_statement(code, title, d.get("background"), d.get("description"),
                         d.get("input_format"), d.get("output_format"), d.get("hint"))

    return {
        "pid": code,
        "title": title,
        "difficulty_id": int(diff_id) if diff_id is not None else None,
        "difficulty": diff,
        "tags": tags,
        "sections": {
            "background": (d.get("background") or "").strip(),
            "description": (d.get("description") or "").strip(),
            "input_format": (d.get("input_format") or "").strip(),
            "output_format": (d.get("output_format") or "").strip(),
            "hint": (d.get("hint") or "").strip(),
        },
        "samples": samples,
        "markdown": md,
        "time_limit_ms": d.get("time_limit_ms"),
        "source": "gesp",
        "source_url": d.get("source_url") or "",
        "license_note": "题面著作权归洛谷及原出题人所有；仅用于非商业毕业设计演示，禁止再分发。",
        "package_level": "C",
        "has_testdata": False,
    }


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--sql", required=True)
    ap.add_argument("--out", default="data/gesp")
    ap.add_argument("--tags", default="data/tags.json")
    args = ap.parse_args(argv)

    sql = Path(args.sql).read_text(encoding="utf-8", errors="replace")
    cols = parse_columns(sql)
    print(f"列: {cols}")

    tag_map: dict[str, str] = {}
    tp = Path(args.tags)
    if tp.exists():
        tag_map = {str(k): v for k, v in json.loads(tp.read_text(encoding="utf-8")).items()}
        print(f"标签映射: {len(tag_map)} 条")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rows = 0
    for m in re.finditer(r"INSERT INTO `problems` VALUES (.*?);\s*$", sql, re.M | re.S):
        for tup in split_tuples(m.group(1)):
            vals = [decode(t) for t in split_fields(tup)]
            if len(vals) != len(cols):
                print(f"[skip] 字段数不符: {len(vals)} != {len(cols)}")
                continue
            rec = to_record(cols, vals, tag_map)
            if not rec["pid"]:
                continue
            (out / f"{rec['pid']}.json").write_text(
                json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            rows += 1

    print(f"转换完成: {rows} 题 -> {out.resolve()}")
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
