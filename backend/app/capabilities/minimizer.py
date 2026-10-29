"""反例最小化（delta debugging）。

为什么要最小化：
    官方测试点动辄十万行，直接丢给学生没有教学价值。
    ddmin 把"能让程序出错的输入"缩到能手算的规模（目标 ≤10 行），
    这是"从判题结果到可讲解证据"的关键一步。

设计：
    - 与题目解耦：调用方提供 `test(items) -> bool`（True 表示仍复现错误）
    - 每一步都跑"参考解"和"学生解"：参考解输出即期望输出，避免把答案写死
    - 按**下标**切块，天然支持重复元素
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

TestFn = Callable[[list[str]], Awaitable[bool]]


@dataclass
class MinimizeResult:
    items: list[str]
    steps: int = 0
    original_size: int = 0
    elapsed_ms: int = 0
    history: list[tuple[int, bool]] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.items)

    @property
    def reduction(self) -> float:
        return 0.0 if not self.original_size else 1 - self.size / self.original_size


async def ddmin(items: list[str], test: TestFn, *, max_steps: int = 80) -> MinimizeResult:
    """经典 ddmin：反复尝试删掉 1/n 的块，直到删不动为止。"""
    t0 = time.time()
    original = len(items)
    cur = list(items)
    n = 2
    steps = 0
    history: list[tuple[int, bool]] = []

    while len(cur) >= 2 and steps < max_steps:
        chunk = max(1, len(cur) // n)
        reduced = False

        for start in range(0, len(cur), chunk):
            if steps >= max_steps:
                break
            end = min(start + chunk, len(cur))
            candidate = cur[:start] + cur[end:]
            if not candidate:
                continue
            steps += 1
            ok = await test(candidate)
            history.append((len(candidate), ok))
            if ok:
                cur = candidate
                n = max(n - 1, 2)
                reduced = True
                break

        if not reduced:
            if n >= len(cur):
                break
            n = min(n * 2, len(cur))

    return MinimizeResult(items=cur, steps=steps, original_size=original,
                          elapsed_ms=int((time.time() - t0) * 1000), history=history)


async def shrink_array_sum(
    values: list[str],
    *,
    runner,
    student_id: str,
    reference_id: str,
    max_steps: int = 80,
) -> MinimizeResult:
    """数组求和类题目的适配器：items 是数组元素，输入按 n + 一行重建。"""

    async def test(candidate: list[str]) -> bool:
        stdin = render_array(candidate)
        ref = await runner.run(reference_id, stdin=stdin, time_limit_ms=3000)
        if ref.status != "Accepted" or ref.exit_code != 0:
            return False
        stu = await runner.run(student_id, stdin=stdin, time_limit_ms=3000)
        if stu.status != "Accepted" or stu.exit_code != 0:
            return False
        return stu.stdout.strip() != ref.stdout.strip()

    return await ddmin(values, test, max_steps=max_steps)


def render_array(values: list[str]) -> str:
    return f"{len(values)}\n" + " ".join(values) + "\n"


def summarize(raw: str) -> dict:
    lines = raw.count("\n") + (0 if raw.endswith("\n") else 1)
    return {"lines": lines, "chars": len(raw)}


async def shrink_t_lines(
    values: list[str],
    *,
    runner,
    student_id: str,
    reference_id: str,
    max_steps: int = 80,
) -> MinimizeResult:
    """多组测试（T 行）适配器：items 是每组数据，输入按 T + 每行重建。"""

    async def test(candidate: list[str]) -> bool:
        stdin = f"{len(candidate)}\n" + "\n".join(candidate) + "\n"
        ref = await runner.run(reference_id, stdin=stdin, time_limit_ms=3000)
        if ref.status != "Accepted" or ref.exit_code != 0:
            return False
        stu = await runner.run(student_id, stdin=stdin, time_limit_ms=3000)
        if stu.status != "Accepted" or stu.exit_code != 0:
            return False
        return stu.stdout.strip() != ref.stdout.strip()

    return await ddmin(values, test, max_steps=max_steps)
