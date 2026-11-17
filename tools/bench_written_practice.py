"""在独立 MySQL 与本机 18002 测量初赛客观题的读题、交卷和重试。"""

import argparse
import asyncio
import json
import os
import statistics
import sys
from math import ceil
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:18002"


def stats(samples, operation):
    selected = [sample for sample in samples if sample[0] == operation]
    values = sorted(sample[1] for sample in selected)
    return {
        "requests": len(selected),
        "errors": sum(error is not None for _, _, error in selected),
        "p50_ms": round(statistics.median(values), 1),
        "p95_ms": round(values[ceil(len(values) * 0.95) - 1], 1),
        "max_ms": round(values[-1], 1),
        "failures": [error for _, _, error in selected if error is not None],
    }


async def measure(client, headers, question, index, operation):
    started = perf_counter()
    error = None
    try:
        if operation == "read":
            response = await client.get("/api/v1/written/questions", headers=headers[index])
            response.raise_for_status()
            if any("answer" in item for item in response.json()["items"]):
                error = "catalog_contains_answer"
        else:
            correct = index % 2 == 0
            response = await client.post("/api/v1/written/attempts", headers=headers[index], json={
                **question,
                "request_id": str(uuid4()),
                "response": {"kind": "text", "value": "2" if correct else "4"},
            })
            response.raise_for_status()
            result = response.json()
            if result["correct"] != correct or result["earned_score"] != (2 if correct else 0):
                error = "score_mismatch"
    except httpx.HTTPStatusError as failure:
        error = f"HTTP {failure.response.status_code}"
    except (httpx.HTTPError, ValueError, KeyError) as failure:
        error = type(failure).__name__
    return operation, (perf_counter() - started) * 1000, error


async def benchmark(question_id):
    from app.core.security import create_access_token
    from app.domain.models import User
    from app.infra.db import SessionLocal, dispose_engine

    try:
        async with SessionLocal() as session:
            marker = uuid4().hex[:12]
            students = [User(username=f"bench_written_{marker}_{i}", role="student") for i in range(100)]
            session.add_all(students)
            await session.commit()
            headers = [
                {"Authorization": "Bearer " + create_access_token(s.id, s.role, s.username, s.token_version)}
                for s in students
            ]
        async with httpx.AsyncClient(
            base_url=BASE_URL, timeout=20, trust_env=False,
            limits=httpx.Limits(max_connections=110, max_keepalive_connections=100),
        ) as client:
            identity = await client.get("/api/v1/auth/me", headers=headers[0])
            identity.raise_for_status()
            if identity.json()["username"] != students[0].username:
                raise RuntimeError("18002 端口的服务未连接到同一个测试库")
            question = await client.get(f"/api/v1/written/questions/{question_id}", headers=headers[0])
            question.raise_for_status()
            data = question.json()
            if data["question_type"] != "fill_blank" or "a[2] 到 a[4]" not in data["stem"]:
                raise RuntimeError("请使用原创演示包的 demo-s-prefix-sum 填空题")
            target = {"question_id": question_id, "version_id": data["version_id"]}

            await asyncio.gather(*(measure(client, headers, target, i, "read") for i in range(10)))
            scenarios = {
                "100_reads": [(i, "read") for i in range(100)],
                "30_submissions": [(i, "submit") for i in range(30)],
                "mixed": [(i, "submit" if i < 30 else "read") for i in range(100)],
            }
            report = {}
            for name, batch in scenarios.items():
                samples = []
                for _ in range(3):
                    results = await asyncio.gather(*(
                        measure(client, headers, target, i, operation) for i, operation in batch
                    ))
                    samples.extend(results)
                report[name] = {operation: stats(samples, operation) for operation in ("read", "submit")
                                if any(sample[0] == operation for sample in samples)}
            replay_request = {**target, "request_id": str(uuid4()), "response": {"kind": "text", "value": "2"}}
            duplicate = await asyncio.gather(*(
                client.post("/api/v1/written/attempts", headers=headers[0], json=replay_request)
                for _ in range(2)
            ))
            for receipt in duplicate:
                receipt.raise_for_status()
            first, second = (receipt.json() for receipt in duplicate)
            report["idempotent_retry"] = (
                first["id"] == second["id"] and first["replayed"] != second["replayed"]
            )
            print(json.dumps(report, indent=2))
            return 0 if report["idempotent_retry"] and all(
                item["errors"] == 0 for case in scenarios for item in report[case].values()
            ) else 1
    finally:
        await dispose_engine()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question-id", type=int, required=True, help="已审核发布的原创前缀和填空题编号")
    arguments = parser.parse_args()
    configured = os.environ.get("TEST_DATABASE_URL")
    if not configured:
        parser.error("请通过 TEST_DATABASE_URL 指定独立 MySQL 测试库")
    url = make_url(configured)
    if url.drivername != "mysql+asyncmy" or not (url.database or "").startswith("test_"):
        parser.error("MySQL 测试库的名称须以 test_ 开头")
    if arguments.question_id < 1:
        parser.error("题目编号必须为正数")
    os.environ["DATABASE_URL"] = configured
    os.environ["LLM_ENABLED"] = "false"
    os.environ["JUDGE_WORKER_ENABLED"] = "false"
    os.environ["MAINTENANCE_WORKER_ENABLED"] = "false"
    sys.path.insert(0, str(ROOT / "backend"))
    return asyncio.run(benchmark(arguments.question_id))


if __name__ == "__main__":
    raise SystemExit(main())
