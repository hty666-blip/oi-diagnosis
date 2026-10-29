import urllib.request, http.cookiejar, lxml.html, time, re

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
op.addheaders = [("User-Agent", UA), ("Accept-Language", "zh-CN,zh;q=0.9")]

def get(url):
    with op.open(url, timeout=20) as r:
        return r.read().decode("utf-8", "replace")

html = get("https://www.luogu.com.cn/problem/P1001")
doc = lxml.html.fromstring(html)
print("title:", doc.findtext(".//title"))
for el in doc.iter():
    if not isinstance(el.tag, str):
        continue
    cls = el.get("class") or ""
    if not cls:
        continue
    txt = (el.text_content() or "").strip()
    if len(txt) > 60:
        print(f"  <{el.tag} class='{cls}'> len={len(txt)} :: {txt[:50]!r}")
time.sleep(2)

lst = get("https://www.luogu.com.cn/problem/list?page=1")
ld = lxml.html.fromstring(lst)
links = [(a.get("href"), (a.text_content() or "").strip()) for a in ld.iter("a")
         if (a.get("href") or "").startswith("/problem/") and len((a.text_content() or "").strip()) > 1]
print("\nlist links:", len(links))
for h, t in links[:8]:
    print("  ", h, t[:40])
