import urllib.request, http.cookiejar, json, re, urllib.parse, time

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
op.addheaders = [("User-Agent", UA), ("Accept-Language", "zh-CN,zh;q=0.9")]

def get(url):
    with op.open(url, timeout=20) as r:
        return r.read().decode("utf-8", "replace")

html = get("https://www.luogu.com.cn/problem/P1001")
m = re.search(r'_feInjection\s*=\s*JSON\.parse\(decodeURIComponent\("(.*?)"\)\)', html, re.S)
print("feInjection found:", bool(m))
if m:
    data = json.loads(urllib.parse.unquote(m.group(1)))
    print("top keys:", list(data.keys()))
    d = data.get("data", {})
    print("data keys:", list(d.keys()))
    p = d.get("problem", {})
    print("problem keys:", list(p.keys()))
    for k in ("pid","title","difficulty","tags","samples","limits","hint","background","description","formatI","formatO"):
        v = p.get(k)
        s = json.dumps(v, ensure_ascii=False)[:160] if not isinstance(v, str) else v[:120]
        print(f"  {k} = {s!r}")

time.sleep(2)
lst = get("https://www.luogu.com.cn/problem/list?page=1&_contentOnly=1")
print("\nlist len:", len(lst), "head:", lst[:200].replace("\n", " "))
try:
    j = json.loads(lst)
    print("list keys:", list(j.keys()), "| problems:", len(j.get("problems", {}).get("result", [])))
    r0 = j["problems"]["result"][0]
    print("first item:", json.dumps(r0, ensure_ascii=False)[:300])
except Exception as e:
    print("list parse fail:", type(e).__name__, e)
