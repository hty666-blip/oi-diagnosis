import urllib.request, http.cookiejar, time

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
opener.addheaders = [("User-Agent", UA), ("Accept-Language", "zh-CN,zh;q=0.9")]

for url in ["https://www.luogu.com.cn/problem/P1001",
            "https://www.luogu.com.cn/problem/P1046"]:
    try:
        with opener.open(url, timeout=20) as r:
            html = r.read().decode("utf-8", "replace")
        print(f"OK {url} status={r.status} bytes={len(html)} cookies={len(cj)}")
        print("  has '题目描述':", "题目描述" in html, "| has 'A+B':", "A+B" in html)
    except Exception as e:
        print(f"FAIL {url}: {type(e).__name__}: {e}")
    time.sleep(2.0)
