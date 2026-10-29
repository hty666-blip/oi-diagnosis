#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抓取洛谷题目标签 id -> 名称映射，并回填已导入的题目 JSON。

用法:
    python tools/luogu_tags.py --out data/tags.json --remap data/problems
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/tags.json")
    ap.add_argument("--remap", default="data/problems")
    ap.add_argument("--delay", type=float, default=2.0)
    args = ap.parse_args(argv)

    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", DEFAULT_UA), ("Accept-Language", "zh-CN,zh;q=0.9")]

    with op.open("https://www.luogu.com.cn/problem/list", timeout=25) as r:
        html = r.read().decode("utf-8", "replace")

    tags: dict[str, str] = {}
    for m in re.finditer(r'href="/problem/list\?tag=(\d+)"[^>]*>(.*?)</a>', html, re.S):
        name = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if name and not name.isdigit():
            tags[m.group(1)] = name

    Path(args.out).write_text(json.dumps(tags, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"标签映射: {len(tags)} 条 -> {args.out}")

    remapped = 0
    for p in Path(args.remap).glob("*.json"):
        rec = json.loads(p.read_text(encoding="utf-8"))
        new = []
        for t in rec.get("tags", []):
            if isinstance(t, str) and t.startswith("tag:"):
                new.append(tags.get(t[4:], t))
            else:
                new.append(t)
        if new != rec.get("tags"):
            rec["tags"] = new
            p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            remapped += 1
    print(f"回填题目: {remapped} 个")
    time.sleep(args.delay)
    return 0


if __name__ == "__main__":
    sys.exit(main())
