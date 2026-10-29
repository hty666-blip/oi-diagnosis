import pytest

from app.capabilities.minimizer import ddmin, render_array, summarize


@pytest.mark.asyncio
async def test_ddmin_finds_minimal_subset():
    # 只要包含 "boom" 就失败 → 最小反例就是 ["boom"]
    async def test(items):
        return "boom" in items

    items = ["a", "b", "boom", "c", "d"]
    res = await ddmin(items, test, max_steps=50)
    assert res.items == ["boom"]
    assert res.original_size == 5
    assert res.reduction == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_ddmin_handles_duplicates_by_index():
    # 需要"至少两个 x"才失败，重复元素不能被一次性抹掉
    async def test(items):
        return items.count("x") >= 2

    res = await ddmin(["x", "y", "x", "z"], test, max_steps=50)
    assert res.items.count("x") == 2
    assert len(res.items) == 2


@pytest.mark.asyncio
async def test_ddmin_respects_max_steps():
    async def test(items):
        return len(items) > 1

    res = await ddmin(list("abcdefghij"), test, max_steps=3)
    assert res.steps <= 3


@pytest.mark.asyncio
async def test_ddmin_never_returns_empty():
    async def test(items):
        return True

    res = await ddmin(["a", "b"], test, max_steps=10)
    assert res.items


def test_render_array_format():
    assert render_array(["1", "2"]) == "2\n1 2\n"


def test_summarize_counts_lines():
    assert summarize("2\n1 2\n") == {"lines": 2, "chars": 6}
