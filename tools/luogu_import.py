#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
洛谷题面导入器（仅用于毕业设计 / 本地教学演示）

用法:
    python tools/luogu_import.py --ids P1001,P1046 --out data/problems --force
    python tools/luogu_import.py --file ids.txt --delay 2.5

实现说明:
  - 走洛谷前端的 content-only 接口（请求头 x-lentille-request: content-only），
    返回 JSON，比解析 HTML 稳定；失败自动回退到 HTML 解析。
  - 只访问 /problem/{pid}；robots.txt 明确禁用的是 /record、/recordnew、/paste。
  - 单线程 + 默认 2 秒间隔 + 指数退避；只落本地磁盘。

声明: 题面著作权归洛谷及原出题人所有。本脚本仅用于非商业的毕业设计演示，
      使用者应自行确保符合洛谷用户协议；公开发布或商用请走官方授权。
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import lxml.html

BASE = "https://www.luogu.com.cn/problem/"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

DIFFICULTY = {
    0: "暂无评定", 1: "入门", 2: "普及−", 3: "普及/提高−",
    4: "普及+/提高", 5: "提高+/省选−", 6: "省选/NOI−", 7: "NOI/NOI+/CTSC",
}
# 标签 id -> 名称：优先读 data/tags.json（由 tools/luogu_tags.py 抓取）
TAG_FILE = Path("data/tags.json")


def load_tag_map() -> dict[str, str]:
    if TAG_FILE.exists():
        try:
            return {str(k): v for k, v in json.loads(TAG_FILE.read_text(encoding="utf-8")).items()}
        except Exception:  # noqa: BLE001
            return {}
    return {}
CONTENT_KEYS = {
    "background": "background",
    "description": "description",
    "formatI": "input_format",
    "input_format": "input_format",
    "formatO": "output_format",
    "output_format": "output_format",
    "hint": "hint",
}
SECTION_LABEL = {
    "background": "题目背景",
    "description": "题目描述",
    "input_format": "输入格式",
    "output_format": "输出格式",
    "hint": "说明/提示",
}
HTML_SECTION_MAP = {
    "题目背景": "background", "题目描述": "description",
    "输入格式": "input_format", "输出格式": "output_format",
    "说明/提示": "hint", "说明": "hint",
}


def build_opener(ua: str):
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [
        ("User-Agent", ua),
        ("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8"),
    ]
    return op


def fetch(op, url: str, json_mode: bool = False, retries: int = 3, backoff: float = 6.0):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url)
            if json_mode:
                req.add_header("x-lentille-request", "content-only")
            with op.open(req, timeout=25) as r:
                body = r.read().decode("utf-8", "replace")
            return json.loads(body) if json_mode else body
        except urllib.error.HTTPError as e:
            if e.code in (403, 404, 410):
                raise
            last = e
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(backoff * (2**i))
    raise last  # type: ignore[misc]


def clean_md(text: str) -> str:
    lines = [ln.rstrip() for ln in (text or "").splitlines()]
    lines = [ln[4:] if ln.startswith("    ") else ln for ln in lines]
    out, blank = [], 0
    for ln in lines:
        if not ln.strip():
            blank += 1
            if blank > 1:
                continue
        else:
            blank = 0
        out.append(ln)
    return "\n".join(out).strip()


def record_from_json(pid: str, payload: dict, tag_map: dict[str, str] | None = None) -> dict:
    p = payload["data"]["problem"]
    sections: dict[str, str] = {}
    for k, v in (p.get("contenu") or {}).items():
        key = CONTENT_KEYS.get(k)
        if key and isinstance(v, str) and v.strip():
            sections[key] = clean_md(v)

    samples = []
    for item in (p.get("samples") or []):
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            samples.append({"input": clean_md(item[0]), "output": clean_md(item[1])})

    tag_map = tag_map or {}
    tags = [tag_map.get(str(t), f"tag:{t}") for t in (p.get("tags") or [])]
    return {
        "pid": p.get("pid") or pid,
        "title": (p.get("name") or "").strip(),
        "difficulty_id": p.get("difficulty"),
        "difficulty": DIFFICULTY.get(p.get("difficulty"), "未知"),
        "tags": tags,
        "tags_raw": p.get("tags") or [],
        "sections": sections,
        "samples": samples,
    }


def record_from_html(pid: str, html: str) -> dict:
    doc = lxml.html.fromstring(html)
    h1 = doc.find(".//h1")
    title = (h1.text_content().strip() if h1 is not None else "")
    title = re.sub(rf"^{pid}\s*", "", title).strip()
    sections: dict[str, str] = {}
    for sec in doc.iter("section"):
        h2 = sec.find("h2")
        if h2 is None:
            continue
        key = HTML_SECTION_MAP.get((h2.text_content() or "").strip())
        if not key:
            continue
        div = sec.find("div")
        body = clean_md(div.text_content() if div is not None else sec.text_content())
        if body:
            sections[key] = (sections.get(key, "") + "\n\n" + body).strip()
    return {"pid": pid, "title": title, "difficulty_id": None, "difficulty": "未知",
            "tags": [], "sections": sections, "samples": []}


def to_markdown(rec: dict) -> str:
    parts = [f"# {rec['pid']} {rec['title']}".strip()]
    for key in ("background", "description", "input_format", "output_format", "hint"):
        body = rec["sections"].get(key)
        if body:
            parts.append(f"## {SECTION_LABEL[key]}\n\n{body}")
    if rec["samples"]:
        parts.append("## 输入输出样例")
        for i, s in enumerate(rec["samples"], 1):
            parts.append(f"样例 {i} 输入\n```\n{s['input']}\n```")
            parts.append(f"样例 {i} 输出\n```\n{s['output']}\n```")
    return "\n\n".join(parts).strip() + "\n"


def main(argv=None) -> int:
    try:  # Windows 控制台默认 GBK，避免 '−' 等字符导致打印崩溃
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="洛谷题面导入器（仅毕设本地演示）")
    ap.add_argument("--ids")
    ap.add_argument("--file")
    ap.add_argument("--out", default="data/problems")
    ap.add_argument("--delay", type=float, default=2.0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ua", default=DEFAULT_UA)
    ap.add_argument("--force", action="store_true", help="已存在也重新抓取")
    args = ap.parse_args(argv)

    ids: list[str] = []
    if args.ids:
        ids += [x.strip() for x in args.ids.split(",") if x.strip()]
    if args.file:
        ids += [x.strip() for x in Path(args.file).read_text(encoding="utf-8").splitlines() if x.strip()]
    if not ids:
        ap.error("请用 --ids 或 --file 指定题号")
    if args.delay < 1.0:
        ap.error("--delay 不得小于 1.0 秒")

    out = Path(args.out)
    (out / "raw").mkdir(parents=True, exist_ok=True)
    op = build_opener(args.ua)
    tag_map = load_tag_map()
    if tag_map:
        print(f"已加载标签映射 {len(tag_map)} 条")
    ok = fail = skip = 0

    for pid in ids:
        if args.limit and ok >= args.limit:
            break
        dst = out / f"{pid}.json"
        if dst.exists() and not args.force:
            skip += 1
            print(f"[skip] {pid}")
            continue

        url = BASE + pid
        try:
            try:
                payload = fetch(op, url, json_mode=True)
                rec = record_from_json(pid, payload, tag_map)
                mode = "json"
            except Exception:
                html = fetch(op, url)
                (out / "raw" / f"{pid}.html").write_text(html, encoding="utf-8")
                rec = record_from_html(pid, html)
                mode = "html"

            if not rec["sections"].get("description"):
                raise ValueError("未解析到题目描述")
            rec.update({
                "source": "luogu",
                "source_url": url,
                "license_note": "题面著作权归洛谷及原出题人所有；仅用于非商业毕业设计演示，禁止再分发。",
                "package_level": "C",
                "has_testdata": False,
                "crawled_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            })
            rec["markdown"] = to_markdown(rec)
            dst.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            ok += 1
            print(f"[ok]   {pid} {rec['title']} [{rec['difficulty']}] "
                  f"tags={rec['tags']} samples={len(rec['samples'])} via={mode}")
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"[fail] {pid}: {type(e).__name__}: {e}")
        time.sleep(args.delay)

    print(f"\n完成: ok={ok} fail={fail} skip={skip} -> {out.resolve()}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
