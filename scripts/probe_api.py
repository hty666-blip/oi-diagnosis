import urllib.request, http.cookiejar, json, time

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
op.addheaders = [("User-Agent", UA), ("Accept-Language", "zh-CN,zh;q=0.9")]

def get(url, extra=None):
    req = urllib.request.Request(url)
    for k, v in (extra or {}).items():
        req.add_header(k, v)
    with op.open(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")

for hdr in ({"x-luogu-type": "content-only"}, {"X-Luogu-Type": "content-only"}):
    html = get("https://www.luogu.com.cn/problem/P1001", hdr)
    print(hdr, len(html), html[:80].replace("\n", " "))
    try:
        j = json.loads(html)
        print("  JSON ok, keys:", list(j.keys())[:8])
        p = j.get("currentData", {}).get("problem") or j.get("problem") or {}
        print("  problem keys:", list(p.keys())[:20])
        if p:
            print("  title:", p.get("title"), "| difficulty:", p.get("difficulty"))
            print("  content len:", len(p.get("content") or ""), "| samples:", json.dumps(p.get("samples"), ensure_ascii=False)[:200])
        break
    except Exception as e:
        print("  not json:", type(e).__name__)
    time.sleep(2.0)
