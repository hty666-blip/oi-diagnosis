"""把导师决定说成人话：LLM 负责语气与具体度，规则负责边界。

约束（写进 prompt 也写进代码）：
- 不给完整程序
- 不越过当前层级该透露的信息
- 必须接住学生原话，不能复读上一句
- 长度 60~260 字，面向初学者
"""

from __future__ import annotations

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
    1: "只指出错误属于哪一大类（如边界、类型、复杂度），不给位置。",
    2: "指出大致位置（行号或代码段），但不给具体改法。",
    3: "给一组能复现错误的最小数据，让学生自己跑。",
    4: "给具体的修改方向，但仍不让直接抄完整答案。",
}


async def compose(decision: TutorDecision, diag: DiagnosisView,
                  student_msg: str, llm) -> str:
    """返回最终回复；模型不可用或输出不合规时回退到模板。"""
    fallback = decision.content
    if llm is None or not getattr(llm, "available", False):
        return fallback
    if decision.action in ("refuse", "resolved"):
        return fallback

    user = (
        f"当前提示层级：L{decision.level}（{LEVEL_HINT.get(decision.level, '')}）\n"
        f"本轮任务：{decision.mode}（{decision.reason}）\n"
        f"诊断信息：性质={diag.p}，说明={diag.message or '无'}，"
        f"位置={(f'第 {diag.line} 行') if diag.line else '未定位'}，"
        f"参考建议={diag.hint or '无'}\n"
        f"学生刚说：{student_msg or '（刚开始）'}\n"
        f"模板草稿（可改写，不要照抄）：{fallback[:200]}"
    )

    text = await llm.chat_text(SYSTEM, user, max_tokens=420, temperature=0.7)
    if not text:
        return fallback

    text = text.strip()
    if len(text) < 20 or len(text) > 700:
        return fallback
    if not safe_output(text):
        return fallback
    return text

