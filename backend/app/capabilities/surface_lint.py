#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""表层错误检查器（surface lint）原型

用途:
    在把代码送进沙箱之前，先用**确定性规则**识别初学者的"表层错误"：
    中文标点、全角空格、BOM、中文引号、代码区中文标识符……

    这类错误占初学者提交的很大比例，且完全不需要 LLM 判断——
    编译器的报错（stray '\\357'）对新手等于天书，但规则一查就知道是什么。

用法:
    python tools/surface_lint.py --file examples/bad.cpp
    python tools/surface_lint.py --file a.cpp --fix -o a.fixed.cpp
    python tools/surface_lint.py --gcc-log build.log

退出码: 0 = 无问题；1 = 有问题
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

# 全角/中文标点 -> 半角（仅在“代码区”替换）
FULLWIDTH_MAP = {
    "；": ";", "，": ",", "（": "(", "）": ")", "｛": "{", "｝": "}",
    "［": "[", "］": "]", "【": "[", "】": "]", "“": '"', "”": '"',
    "‘": "'", "’": "'", "：": ":", "。": ".", "、": ",", "！": "!",
    "？": "?", "《": "<", "》": ">", "　": " ", "＋": "+", "－": "-",
    "＊": "*", "／": "/", "％": "%", "＝": "=", "＜": "<", "＞": ">",
    "＆": "&", "～": "~", "＃": "#", "＄": "$", "＠": "@",
    "＾": "^", "＿": "_", "｀": "`", "｜": "|", "·": ".",
}

# 常见编译器报错 -> 学生看得懂的解释
GCC_HINTS = [
    (r"stray '\\\d+' in program",
     "代码里出现了编译器不认识的非 ASCII 字符，最常见的是**中文标点**或文件开头的 BOM。",
     "检查该行附近是否有 `；，（）“”` 等全角符号，改成英文半角。"),
    (r"error: '(\w+)' was not declared in this scope",
     "用了未声明的名字 `{0}`：可能是变量名拼错、忘了定义，或没 include 对应头文件。",
     "检查拼写；如果用 `cin/cout`，确认已 `#include <iostream>` 且写了 `using namespace std;`。"),
    (r"expected ';' before",
     "上一行结尾少了分号（或者这一行开头多了东西）。",
     "看报错位置的**上一行末尾**，补上 `;`（注意是英文分号）。"),
    (r"expected '}' at end of input",
     "括号没有配对：少了一个 `}`。",
     "用编辑器的括号匹配功能，从 `main` 开始逐层检查。"),
    (r"expected '\)' before",
     "括号没有配对：少了一个 `)`。",
     "检查函数调用与 `if/while` 条件的括号。"),
    (r"invalid conversion from 'const char\*' to 'int'",
     "把字符串当成了整数用（常见于 `cin >> 变量` 写错，或 `printf` 格式符不匹配）。",
     "检查变量类型与输入/输出方式是否一致。"),
    (r"'(\w+)' does not name a type",
     "这里应该是类型名，但编译器不认识 `{0}`。",
     "检查是否漏了 `#include`，或把变量写在了类型的位置。"),
    (r"redefinition of '(\w+)'",
     "`{0}` 被重复定义了。",
     "改个名字，或删掉重复定义（注意 `main` 只能有一个）。"),
    (r"division by zero",
     "除数为 0。",
     "检查除法/取模的除数是否可能为 0，尤其是变量。"),
]

SEVERITY = {"P0": "表层（输入法/笔误）", "P1": "语法（语言规则）"}


@dataclass
class Finding:
    code: str
    line: int
    col: int
    char: str
    codepoint: str
    message: str
    suggestion: str
    severity: str = "P0"
    autofix: str | None = None


def scan(src: str) -> list[Finding]:
    """逐字符扫描，区分代码区 / 字符串 / 注释，只在代码区报错。"""
    findings: list[Finding] = []
    state = "code"          # code | line_comment | block_comment | string | char
    i, line, col = 0, 1, 1
    n = len(src)

    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""

        if ch == "\n":
            line += 1
            col = 1
            if state == "line_comment":
                state = "code"
            i += 1
            continue

        if state == "code":
            if ch == "/" and nxt == "/":
                state = "line_comment"; i += 2; col += 2; continue
            if ch == "/" and nxt == "*":
                state = "block_comment"; i += 2; col += 2; continue
            if ch == '"':
                state = "string"; i += 1; col += 1; continue
            if ch == "'":
                state = "char"; i += 1; col += 1; continue

            if not ch.isascii():
                fix = FULLWIDTH_MAP.get(ch)
                if fix is None and "\u4e00" <= ch <= "\u9fff":
                    findings.append(Finding(
                        code="P0-CJK-IDENT", line=line, col=col, char=ch,
                        codepoint=f"U+{ord(ch):04X}",
                        message=f"代码区出现中文字符「{ch}」",
                        suggestion="可能是输入法没切回来；变量名/函数名建议用英文。",
                    ))
                elif fix is not None:
                    findings.append(Finding(
                        code="P0-FULLWIDTH", line=line, col=col, char=ch,
                        codepoint=f"U+{ord(ch):04X}",
                        message=f"中文/全角符号「{ch}」不能用于 C++ 语法",
                        suggestion=f"改成英文半角「{fix}」",
                        autofix=fix,
                    ))
                else:
                    findings.append(Finding(
                        code="P0-NONASCII", line=line, col=col, char=ch,
                        codepoint=f"U+{ord(ch):04X}",
                        message=f"代码区出现非 ASCII 字符「{ch}」",
                        suggestion="确认是否误输入；若是分隔符请改成英文半角。",
                    ))

        elif state == "string":
            if ch == "\\":
                i += 2; col += 2; continue
            if ch == '"':
                state = "code"
        elif state == "char":
            if ch == "\\":
                i += 2; col += 2; continue
            if ch == "'":
                state = "code"
        elif state == "block_comment":
            if ch == "*" and nxt == "/":
                state = "code"; i += 2; col += 2; continue

        i += 1
        col += 1

    return findings


def check_bom(raw: bytes) -> list[Finding]:
    if raw.startswith(b"\xef\xbb\xbf"):
        return [Finding("P0-BOM", 1, 1, "\\ufeff", "U+FEFF",
                        "文件开头有 UTF-8 BOM，部分编译器会报 stray '\\357'",
                        "用编辑器另存为「UTF-8 无 BOM」", autofix="")]
    return []


def translate_gcc(log: str) -> list[dict]:
    out = []
    for line in log.splitlines():
        if "error:" not in line and "warning:" not in line:
            continue
        for pat, msg, hint in GCC_HINTS:
            m = re.search(pat, line)
            if m:
                detail = msg.format(*m.groups()) if m.groups() else msg
                out.append({"raw": line.strip(), "meaning": detail, "hint": hint})
                break
    return out


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    ap = argparse.ArgumentParser(description="表层错误检查器（规则，不经 LLM）")
    ap.add_argument("--file", help="待检查的源码")
    ap.add_argument("--gcc-log", help="编译器 stderr 日志")
    ap.add_argument("--fix", action="store_true", help="自动修复全角符号（不改字符串/注释）")
    ap.add_argument("-o", "--output", help="修复后写入的文件")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = ap.parse_args(argv)

    if not args.file and not args.gcc_log:
        ap.error("至少给 --file 或 --gcc-log")

    findings: list[Finding] = []
    if args.file:
        raw = Path(args.file).read_bytes()
        findings += check_bom(raw)
        src = raw.decode("utf-8", errors="replace")
        findings += scan(src)

    if args.json:
        payload = {
            "findings": [asdict(f) for f in findings],
            "gcc": translate_gcc(Path(args.gcc_log).read_text(encoding="utf-8", errors="replace")) if args.gcc_log else [],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if findings:
            print(f"发现 {len(findings)} 个表层问题：\n")
            for f in findings:
                print(f"  [{f.severity}] 第 {f.line} 行 第 {f.col} 列  {f.message}")
                print(f"         {f.codepoint}  →  {f.suggestion}")
        else:
            print("未发现表层问题（字符层面干净）")

        if args.gcc_log:
            items = translate_gcc(Path(args.gcc_log).read_text(encoding="utf-8", errors="replace"))
            print(f"\n编译器报错翻译（{len(items)} 条）：")
            for it in items:
                print(f"  · {it['raw']}")
                print(f"    意思：{it['meaning']}")
                print(f"    怎么办：{it['hint']}")

    if args.fix and args.file:
        src = Path(args.file).read_bytes().decode("utf-8", errors="replace")
        # 只修“代码区”的全角字符：复用扫描逻辑定位
        chars = list(src)
        for f in findings:
            if f.code == "P0-FULLWIDTH" and f.autofix is not None:
                idx = _index_of(chars, f.line, f.col)
                if idx is not None:
                    chars[idx] = f.autofix
        out = args.output or str(Path(args.file).with_suffix(".fixed.cpp"))
        Path(out).write_text("".join(chars), encoding="utf-8")
        print(f"\n已修复并写入: {out}")

    return 1 if findings else 0


def _index_of(chars: list[str], line: int, col: int) -> int | None:
    cur_line, cur_col = 1, 1
    for i, ch in enumerate(chars):
        if cur_line == line and cur_col == col:
            return i
        if ch == "\n":
            cur_line += 1
            cur_col = 1
        else:
            cur_col += 1
    return None


if __name__ == "__main__":
    sys.exit(main())

