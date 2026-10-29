import urllib.request, http.cookiejar

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
op.addheaders = [("User-Agent", UA), ("Accept-Language", "zh-CN,zh;q=0.9")]
with op.open("https://www.luogu.com.cn/problem/P1001", timeout=20) as r:
    html = r.read().decode("utf-8", "replace")

i = html.find("题目描述")
print("len:", len(html), "idx:", i)
print("---- window ----")
print(html[max(0, i-600): i+900])
