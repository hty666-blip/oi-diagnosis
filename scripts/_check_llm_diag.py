"""对本地 API 做一次带鉴权的模型诊断烟雾检查。

运行前先启动后端并执行 ``scripts/seed_demo.py``。脚本只用于本地开发，
默认使用 seed_demo 创建的学生演示账号；也可通过参数或环境变量覆盖。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

import httpx

DEFAULT_BASE = "http://127.0.0.1:8000/api/v1"

CASES = {
    "wa_no_rule": """#include <cstdio>
int main(){int a,b;scanf("%d%d",&a,&b);printf("%d\\n",a-b);return 0;}
""",
    "wa_logic": """#include <cstdio>
int main(){int n;scanf("%d",&n);int s=0;for(int i=1;i<n;i++)s+=i;printf("%d\\n",s);return 0;}
""",
}


class SmokeCheckError(Exception):
    """本地烟雾检查无法继续。"""


def response_json(response: httpx.Response, action: str) -> dict[str, Any]:
    """解析 API 响应，并把空响应、HTML 和 HTTP 错误转成可操作的提示。"""
    content_type = response.headers.get("content-type", "未提供")
    try:
        payload = response.json()
    except ValueError as exc:
        preview = response.text[:300].replace("\r", " ").replace("\n", " ").strip()
        if not preview:
            preview = "（空响应）"
        raise SmokeCheckError(
            f"{action}返回了非 JSON 响应：HTTP {response.status_code}，"
            f"Content-Type={content_type}，响应摘要={preview}"
        ) from exc

    if response.is_error:
        detail = payload.get("detail") if isinstance(payload, dict) else payload
        raise SmokeCheckError(f"{action}失败：HTTP {response.status_code}，detail={detail}")
    if not isinstance(payload, dict):
        raise SmokeCheckError(
            f"{action}响应结构无效：期望 JSON 对象，实际为 {type(payload).__name__}"
        )
    return payload


def request_json(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    action: str,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        response = client.request(method, path, **kwargs)
    except httpx.RequestError as exc:
        raise SmokeCheckError(
            f"{action}无法连接本地 API（{client.base_url}）。"
            "请先启动 Docker Desktop，再在项目根目录运行 .\\scripts\\dev.ps1。"
        ) from exc
    return response_json(response, action)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("OI_API_BASE", DEFAULT_BASE))
    parser.add_argument("--username", default=os.getenv("OI_TEST_USERNAME", "student01"))
    parser.add_argument("--password", default=os.getenv("OI_TEST_PASSWORD", "123456"))
    parser.add_argument("--problem", default=os.getenv("OI_TEST_PROBLEM", "P1001"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        with httpx.Client(base_url=args.base_url.rstrip("/") + "/", timeout=180) as client:
            auth = request_json(
                client,
                "POST",
                "auth/login",
                action="登录",
                json={"username": args.username, "password": args.password},
            )
            token = auth.get("access_token")
            if not isinstance(token, str) or not token:
                raise SmokeCheckError("登录响应缺少 access_token，请检查后端版本")
            client.headers["Authorization"] = f"Bearer {token}"

            for name, code in CASES.items():
                started = time.perf_counter()
                sub = request_json(
                    client,
                    "POST",
                    "submissions",
                    action=f"提交 {name}",
                    json={"problem_pid": args.problem, "code": code},
                )
                submission_id = sub.get("id")
                if not isinstance(submission_id, int):
                    raise SmokeCheckError(f"提交 {name} 的响应缺少整数 id")
                judged_ms = int((time.perf_counter() - started) * 1000)

                started = time.perf_counter()
                out = request_json(
                    client,
                    "POST",
                    f"submissions/{submission_id}/diagnose",
                    action=f"诊断 {name}",
                )
                diag_ms = int((time.perf_counter() - started) * 1000)

                print(
                    f"\n[{name}] verdict={sub.get('verdict')} "
                    f"判题={judged_ms}ms 诊断={diag_ms}ms"
                )
                print(
                    f"  status={out.get('status')} route={out.get('route')} "
                    f"conf={out.get('confidence')} attempts={out.get('attempts')}"
                )
                print(f"  narrative: {(out.get('narrative') or '')[:120]}")
                for mechanism in (out.get("mechanism") or [])[:2]:
                    print(
                        f"  mechanism: {mechanism.get('code')} "
                        f"conf={mechanism.get('confidence')} "
                        f"ev={mechanism.get('evidence_ids')}"
                    )
    except SmokeCheckError as exc:
        print(f"检查失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
