import sys, json, time

def probe(url, ua=None):
    import urllib.request
    req = urllib.request.Request(url, headers={
        "User-Agent": ua or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return {"url": url, "status": r.status, "bytes": len(raw),
                    "ctype": r.headers.get("Content-Type"), "ms": int((time.time()-t0)*1000),
                    "head": raw[:300].decode("utf-8", "replace")}
    except Exception as e:
        return {"url": url, "error": f"{type(e).__name__}: {e}"}

for mod in ("requests", "bs4", "lxml", "yaml"):
    try:
        __import__(mod); print(f"lib {mod}: OK")
    except Exception as e:
        print(f"lib {mod}: MISSING ({type(e).__name__})")

targets = [
    "https://www.noi.cn/robots.txt",
    "https://codeforces.com/robots.txt",
    "https://www.luogu.com.cn/problem/P1001",
]
for t in targets:
    r = probe(t)
    print(json.dumps({k: v for k, v in r.items() if k != "head"}, ensure_ascii=False))
    if "head" in r:
        print("   HEAD:", repr(r["head"][:200]))
    time.sleep(1.5)
