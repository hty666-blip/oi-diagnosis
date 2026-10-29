import base64
import json

import httpx

J = "http://localhost:5050"


def show(tag, payload):
    r = httpx.post(f"{J}/run", json=payload, timeout=60)
    data = r.json()
    item = data[0] if isinstance(data, list) else data["results"][0]
    files = {}
    for k, v in (item.get("files") or {}).items():
        try:
            files[k] = base64.b64decode(v).decode("utf-8", "replace")[:200]
        except Exception:
            files[k] = "<bin>"
    print(f"[{tag}] status={item.get('status')} exit={item.get('exitStatus')} "
          f"err={item.get('error')!r} files={files}")


base = {"timeLimit": 5_000_000_000, "memoryLimit": 512 * 1024 * 1024, "procLimit": 64}

show("minimal-echo", {**base, "cmd": [{"args": ["/bin/echo", "hi"]}]})

show("echo+env", {**base, "cmd": [{"args": ["/bin/echo", "hi"], "env": ["PATH=/usr/bin:/bin"]}]})

show("ls-gpp", {**base, "cmd": [{"args": ["/bin/ls", "-l", "/usr/bin/g++"]}],
                "copyOut": ["stdout", "stderr"]})

show("gpp-version", {**base, "cmd": [{"args": ["/usr/bin/g++", "--version"]}],
                     "copyOut": ["stdout", "stderr"]})

cpp = 'int main(){return 0;}'
show("compile-simple", {**base,
                        "cmd": [{"args": ["/usr/bin/g++", "-O2", "-std=c++17", "-o", "a.out", "m.cpp"],
                                 "files": [{"name": "stdout", "max": 4096},
                                           {"name": "stderr", "max": 65536}]}],
                        "copyIn": {"m.cpp": {"content": cpp}},
                        "copyOut": ["stdout", "stderr"],
                        "copyOutCached": ["a.out"]})
