"""把导师决定说成人话：LLM 负责语气与具体度，规则负责边界。

约束（写进 prompt 也写进代码）：
- 不给完整程序
- 不越过当前层级该透露的信息
- 必须接住学生原话，不能复读上一句
- 长度 60~260 字，面向初学者
"""

from __future__ import annotations

import re

from app.agents.tutor import DiagnosisView, TutorDecision, safe_output

SYSTEM = """你是信息学竞赛教练，正在带一名初学者改一道错题。
要求：
1. 语气温和、口语化，先接住学生的情绪或原话，再讲技术。
2. 只讲当前提示层级该讲的内容，不要提前泄露更具体的改法。
3. 绝对不要输出完整程序；最多给 1~2 行片段，且不能是完整 main。
4. 中文，80~220 字，不要分很多条；可以用 1、2 编号。
5. 结尾给一个当下就能做的小动作（看一眼、跑一组数据、打印一个变量）。
6. 不要重复学生已经听过的原话；每轮换一种说法。
7. **不要编造具体测试数据或题目里没有的数字**：需要举例时只能用上面"参考建议"里给出的内容；
   没有就改用描述性的说法（"只留一个元素""把最大值试一下"）。"""

LEVEL_HINT = {
    0: "让学生在没有任何提示的情况下说出自己的思路。",
    1: "仅复述已有证据支持的方向；原因未知时保持未知，不给位置或改法。",
    2: "仅在证据已有定位时指出位置，不给具体改法；未定位时引导比较运行步骤。",
    3: "仅使用已经保存的失败输入；未缩减的数据不能称最小反例，不编造数字。",
    4: "给具体的修改方向，但仍不让直接抄完整答案。",
}


async def compose(decision: TutorDecision, diag: DiagnosisView,
                  student_msg: str, llm) -> str:
    """返回最终回复；模型不可用或输出不合规时回退到模板。"""
    fallback = decision.content if safe_output(decision.content) else "先核对本次运行证据，说明实际与预期哪里不同。"
    if llm is None or not getattr(llm, "available", False):
        return fallback
    if decision.action not in ("hint", "explain") or decision.mode == "gate":
        return fallback

    # 模型只拿到当前层级允许的信息，而不是拿到答案后依赖提示词自觉保密。
    visible = ""
    if decision.level == 1 and diag.code and not diag.hypothesis:
        visible = f"已记录的性质分类={diag.p}"
    elif decision.level == 2:
        visible = f"已定位第 {diag.line} 行" if diag.line else "尚未定位具体行"
    elif decision.level == 3:
        label = "已缩减" if diag.counterexample_minimized else "尚未最小化"
        visible = f"真实失败输入（{label}）={diag.counterexample[:2000]!r}" if diag.counterexample else "没有可用失败输入"
    elif decision.level >= 4:
        visible = f"观察记录={diag.evidence_summary[:1600]}"
        if diag.code and not diag.hypothesis:
            visible += f"；已定位说明={diag.message}；参考建议={diag.hint}"

    user = (
        f"当前提示层级：L{decision.level}（{LEVEL_HINT.get(decision.level, '')}）\n"
        f"本轮任务：{decision.mode}（{decision.reason}）\n"
        f"本层可用信息：{visible or '尚未提供定位或修改建议'}\n"
        f"学生刚说：{student_msg or '（刚开始）'}\n"
        f"模板草稿（可改写，不要照抄）：{fallback[:200]}"
    )

    try:
        text = await llm.chat_text(SYSTEM, user, max_tokens=420, temperature=0.7)
    except Exception:  # 模型失败不能打断已经确定的导师策略。
        return fallback
    if not isinstance(text, str) or not text:
        return fallback

    text = text.strip()
    if len(text) < 20 or len(text) > 700:
        return fallback
    if not safe_output(text):
        return fallback
    line_claims = re.findall(r"第\s*(\d+)\s*行", text)
    if line_claims and (decision.level < 2 or not diag.line or any(int(line) != diag.line for line in line_claims)):
        return fallback
    if decision.level < 4 and (
        (diag.hint and diag.hint in text)
        or re.search(r"改成|改为|替换为|删掉|修改为|换成", text)
    ):
        return fallback
    if re.search(r"最小(?:反例|用例|例子|的数据)", text) and not diag.counterexample_minimized:
        return fallback
    if re.search(r"已.{0,8}(通知|同步|转交|请).{0,8}老师|同步给老师|老师.{0,6}已.{0,6}(确认|收到|复核)", text):
        return fallback
    return text

