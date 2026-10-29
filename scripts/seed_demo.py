#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成演示数据：账号 + 模拟学生提交 + 答疑会话。

用法（后端已启动）:
    backend\\.venv\\Scripts\\python.exe scripts\\seed_demo.py
"""
from __future__ import annotations

import asyncio
import random
import sys
from pathlib import Path

import httpx
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.security import hash_password  # noqa: E402
from app.domain.models import User  # noqa: E402
from app.infra.db import SessionLocal, dispose_engine  # noqa: E402

BASE = "http://127.0.0.1:8000/api/v1"
PASSWORD = "123456"

STUDENTS = [
    ("student01", "林小满", "初一 1 班"),
    ("student02", "陈子昂", "初一 1 班"),
    ("student03", "周雨桐", "初一 1 班"),
    ("student04", "赵一鸣", "初二 3 班"),
    ("student05", "孙可儿", "初二 3 班"),
    ("student06", "吴子豪", "初二 3 班"),
    ("student07", "郑思远", "初三 2 班"),
    ("student08", "何佳琪", "初三 2 班"),
]

FULLWIDTH = """#include <cstdio>
int main() {
    int a, b;
    scanf("%d%d", &a, &b)；
    printf("%d\\n", a + b)；
    return 0；
}
"""

MISSING_SEMI = """#include <cstdio>
int main() {
    int a, b
    scanf("%d%d", &a, &b);
    printf("%d\\n", a + b);
    return 0;
}
"""

WRONG_OP = """#include <cstdio>
int main(){int a,b;scanf("%d%d",&a,&b);printf("%d\\n",a-b);return 0;}
"""

PLUS_ONE = """#include <cstdio>
int main(){int a,b;scanf("%d%d",&a,&b);printf("%d\\n",a+b+1);return 0;}
"""

CORRECT = """#include <cstdio>
int main(){int a,b;scanf("%d%d",&a,&b);printf("%d\\n",a+b);return 0;}
"""

DIV_OFF_BY_ONE = """#include <cstdio>
int main(){
    int a; if(scanf("%d",&a)!=1) return 1;
    for(int i=1;i<a;i++) if(a%i==0) printf("%d\\n", i);
    return 0;
}
"""

DIV_CORRECT = """#include <cstdio>
int main(){
    int a; if(scanf("%d",&a)!=1) return 1;
    for(int i=1;i<=a;i++) if(a%i==0) printf("%d\\n", i);
    return 0;
}
"""

POOL = [
    ("P1001", FULLWIDTH), ("P1001", MISSING_SEMI), ("P1001", WRONG_OP),
    ("P1001", PLUS_ONE), ("P1001", CORRECT),
    ("B3953", DIV_OFF_BY_ONE), ("B3953", DIV_CORRECT),
]


async def ensure_users() -> None:
    async with SessionLocal() as s:
        for username, name, klass in STUDENTS:
            user = (await s.execute(select(User).where(User.username == username))).scalar_one_or_none()
            if user is None:
                s.add(User(username=username, display_name=name, role="student",
                           class_name=klass, password_hash=hash_password(PASSWORD)))
        for username, name, role in [("teacher01", "王老师", "teacher"), ("admin", "管理员", "admin")]:
            user = (await s.execute(select(User).where(User.username == username))).scalar_one_or_none()
            if user is None:
                s.add(User(username=username, display_name=name, role=role,
                           class_name="", password_hash=hash_password(PASSWORD)))
        await s.commit()


def login(cli: httpx.Client, username: str) -> str:
    r = cli.post(f"{BASE}/auth/login", json={"username": username, "password": PASSWORD})
    r.raise_for_status()
    return r.json()["access_token"]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    asyncio.run(ensure_users())
    print(f"账号就绪：{len(STUDENTS)} 名学生 + teacher01 + admin（密码 {PASSWORD}）")

    random.seed(42)
    total = 0
    with httpx.Client(timeout=180) as cli:
        for username, _, _ in STUDENTS:
            token = login(cli, username)
            headers = {"Authorization": f"Bearer {token}"}
            for pid, code in random.sample(POOL, k=random.randint(2, 4)):
                r = cli.post(f"{BASE}/submissions", headers=headers,
                             json={"problem_pid": pid, "code": code})
                if r.status_code != 200:
                    print(f"  [{username}] {pid} 提交失败 {r.status_code}: {r.text[:80]}")
                    continue
                sid = r.json()["id"]
                cli.post(f"{BASE}/submissions/{sid}/diagnose", headers=headers)
                total += 1
                print(f"  [{username}] {pid} #{sid} {r.json().get('verdict')}")
    print(f"\n提交完成：{total} 条")

    # 前 3 名学生各走一遍答疑，产生会话与掌握度数据
    with httpx.Client(timeout=180) as cli:
        for username, _, _ in STUDENTS[:3]:
            token = login(cli, username)
            headers = {"Authorization": f"Bearer {token}"}
            r = cli.post(f"{BASE}/submissions", headers=headers,
                         json={"problem_pid": "P1001", "code": WRONG_OP})
            sid = r.json()["id"]
            cli.post(f"{BASE}/submissions/{sid}/diagnose", headers=headers)
            s = cli.post(f"{BASE}/sessions", headers=headers, json={"submission_id": sid})
            if s.status_code != 200:
                print("  会话创建失败", s.status_code, s.text[:100])
                continue
            sess = s.json()["session_id"]
            cli.post(f"{BASE}/sessions/{sess}/messages", headers=headers,
                     json={"content": "我看了下，好像是输出那里算错了"})
            cli.post(f"{BASE}/sessions/{sess}/messages", headers=headers,
                     json={"content": "改好了，过了！"})
            print(f"  [{username}] 会话 #{sess} 完成")

    asyncio.run(dispose_engine())
    print("\n演示数据生成完毕。登录：student01 / teacher01 / admin，密码 123456")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
