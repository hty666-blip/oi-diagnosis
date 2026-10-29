import time

import httpx

BASE = "http://127.0.0.1:8000/api/v1"

CASES = {
    "wa_no_rule": """#include <cstdio>
int main(){int a,b;scanf("%d%d",&a,&b);printf("%d\\n",a-b);return 0;}
""",
    "wa_logic": """#include <cstdio>
int main(){int n;scanf("%d",&n);int s=0;for(int i=1;i<n;i++)s+=i;printf("%d\\n",s);return 0;}
""",
}

for name, code in CASES.items():
    t0 = time.time()
    sub = httpx.post(f"{BASE}/submissions", json={"problem_pid": "P1001", "code": code},
                     timeout=180).json()
    judged = time.time() - t0
    t1 = time.time()
    out = httpx.post(f"{BASE}/submissions/{sub['id']}/diagnose", timeout=180).json()
    diag_ms = int((time.time() - t1) * 1000)

    print(f"\n[{name}] verdict={sub['verdict']} 判题={int(judged*1000)}ms 诊断={diag_ms}ms")
    print(f"  status={out['status']} route={out['route']} conf={out['confidence']} "
          f"attempts={out['attempts']}")
    print(f"  narrative: {(out.get('narrative') or '')[:120]}")
    for m in (out.get("mechanism") or [])[:2]:
        print(f"  mechanism: {m.get('code')} conf={m.get('confidence')} ev={m.get('evidence_ids')}")
