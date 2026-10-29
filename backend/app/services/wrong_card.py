"""错题卡：把一次诊断固化成可复习的卡片（Markdown）。

设计意图：
- 面向"三天后回来看"的场景，不是即时反馈
- 只保留可迁移的东西：错因、关键片段、复盘问题；不贴完整题解（防代做）
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CardInput:
    pid: str
    title: str
    verdict: str = ""
    narrative: str = ""
    nature: list[dict] = field(default_factory=list)
    code_lines: list[str] = field(default_factory=list)
    focus_line: int | None = None
    mastery: dict | None = None
    next_action: str = ""


def _snippet(lines: list[str], focus: int | None, span: int = 3) -> str:
    if not lines:
        return ""
    if focus is None or focus < 1 or focus > len(lines):
        return ""
    start = max(1, focus - span)
    end = min(len(lines), focus + span)
    out = []
    for i in range(start, end + 1):
        mark = ">" if i == focus else " "
        out.append(f"{mark} {i:>3} | {lines[i - 1]}")
    return "```cpp\n" + "\n".join(out) + "\n```"


def render_card(c: CardInput) -> str:
    parts = [f"# 错题卡 · {c.pid} {c.title}".strip(), ""]

    verdict_badge = {"CE": "编译错误", "WA": "答案错误", "TLE": "超时",
                     "MLE": "超内存", "RE": "运行时错误", "AC": "通过"}.get(c.verdict, c.verdict)
    parts.append(f"**判定**：`{c.verdict}`（{verdict_badge}）")

    if c.narrative:
        parts += ["", "## 这次错在哪", "", c.narrative]

    if c.nature:
        parts += ["", "### 具体位置", "", "| 性质 | 编码 | 位置 | 说明 |", "|---|---|---|---|"]
        for n in c.nature[:4]:
            loc = ""
            if n.get("line"):
                loc = f"第 {n['line']} 行" + (f" {n['col']} 列" if n.get("col") else "")
            msg = (n.get("message") or n.get("hint") or "").replace("|", "/")
            parts.append(f"| {n.get('p', '')} | {n.get('code', '')} | {loc or '-'} | {msg} |")

    focus = None
    for n in c.nature:
        if n.get("line"):
            focus = int(n["line"])
            break
    snippet = _snippet(c.code_lines, focus or c.focus_line)
    if snippet:
        parts += ["", "## 当时写的是", "", snippet]

    parts += ["", "## 三天后自测", ""]
    parts.append("1. 这段代码错的那一类问题，换一道题还会犯吗？")
    parts.append("2. 如果题目数据范围再大 10 倍，现在的写法还安全吗？")
    parts.append("3. 不看提示，你能自己造出一组让它出错的数据吗？")

    if c.mastery:
        parts += ["", f"**掌握度**：{c.mastery.get('node', '')} = "
                      f"{c.mastery.get('value', 0):.2f}"
                      f"（不确定度 {c.mastery.get('uncertainty', 0):.2f}）"]

    if c.next_action:
        parts += ["", f"**下一步**：{c.next_action}"]

    parts += ["", "---", "*本卡片由系统生成；完整题解不在卡内，避免直接抄答案。*"]
    return "\n".join(parts) + "\n"
