"""导师策略：**规则决定"给多少"，模型决定"怎么讲"**。

分层职责（v3）：
- `decide()` 是纯规则：判断该升/该停/该讲/该转人工，不生成长文本
- `compose()`（见 agents/compose.py）负责把决定说成人话

这样既不会"机械升到 L4 然后复读"，也不会让模型随意越过教学红线。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_LEVEL = 4
LEVEL_NAMES = {0: "澄清", 1: "方向", 2: "定位", 3: "反例", 4: "修正"}

ASK_FOR_ANSWER = re.compile(
    r"(给我|帮我|替我|把).{0,10}(代码|程序|答案|写法|做法)"
    r"|(帮我|替我|给我).{0,6}(写|做|打|贴|发)"
    r"|(代码|程序).{0,6}(贴|发|写|给)"
    r"|(写|打|贴|发).{0,4}(给我|出来|一份)"
    r"|完整.{0,4}(代码|程序)"
    r"|直接.{0,8}(答案|代码|结论|做法)"
    r"|答案给我|抄一下|怎么写啊"
)
# "不会/没懂"是最重要的信号：不能用它当升级按钮
CONFUSED = re.compile(
    r"(不知道|不会|不懂|不明白|没懂|没思路|真不会|还是不对|看不懂|什么意思|能讲|讲讲|仔细说|详细说)"
)
RESOLVED = re.compile(
    r"改好了|修好了|\bac\s*了|通过了|成功了|(?<![不没])对了"
    r"|(?:样例|测试|评测|题包)(?:都|已经)?过了"
    r"|(?:^|[\s，。！!])(?:已经|终于|都)?过了(?=$|[\s，。！!])", re.I,
)
FULL_PROGRAM = re.compile(r"\b(?:int|signed|auto|void)\s+main\s*\(", re.I)
MEANINGLESS = re.compile(r"^[\s\W_]*$")

PROBES = [
    "你能用自己的话说说，这段代码打算怎么解决问题吗？",
    "换个问法：如果让你手算样例，你会先做哪一步？",
    "你觉得自己最没把握的是哪一块：读入、计算，还是输出？",
]

ACK = {
    "读入": "好，那我们从读入看起。",
    "输入": "好，那我们从输入看起。",
    "计算": "行，我们先盯计算部分。",
    "输出": "好，那就先看输出。",
    "数组": "提到数组了，这条线索有用。",
    "循环": "好，循环是重点。",
    "边界": "边界确实值得怀疑。",
}


@dataclass
class DiagnosisView:
    p: str = "P2"
    code: str = ""
    message: str = ""
    line: int | None = None
    col: int | None = None
    hint: str = ""
    counterexample: str = ""
    evidence_summary: str = ""
    counterexample_minimized: bool = False
    hypothesis: bool = False


@dataclass
class SessionView:
    level: int = 0
    failed_attempts: int = 0
    cheat_flags: int = 0
    status: str = "open"
    has_student_input: bool = False
    turn: int = 0
    stuck_count: int = 0     # 当前层级里学生连续说"不会/不懂"的次数


@dataclass
class TutorDecision:
    action: str          # hint | explain | refuse | verify | escalate
    level: int
    mode: str = "hint"   # probe | hint | explain | walkthrough | refuse
    content: str = ""    # 模板兜底文案（compose 可改写）
    p: str = ""
    reason: str = ""
    dependency_signal: bool = False
    notes: list[str] = field(default_factory=list)


def wants_answer(text: str) -> bool:
    return bool(ASK_FOR_ANSWER.search(text or ""))


def says_resolved(text: str) -> bool:
    return bool(RESOLVED.search(text or ""))


def is_confused(text: str) -> bool:
    return bool(CONFUSED.search(text or ""))


def is_meaningless(text: str) -> bool:
    return bool(MEANINGLESS.match(text or ""))


def decide(session: SessionView, student_message: str, diag: DiagnosisView) -> TutorDecision:
    msg = (student_message or "").strip()

    if says_resolved(msg):
        return TutorDecision(
            action="verify", level=session.level, mode="gate",
            content="请把修改后的代码重新提交，再核验这次执行结果。"
                    "通过公开样例还不能认定整题已经解决。",
            p=diag.p, reason="verification_required",
        )

    if wants_answer(msg):
        return TutorDecision(
            action="refuse", level=session.level, mode="refuse",
            content=_refuse_text(session.level), p=diag.p, reason="ask_for_answer",
            dependency_signal=True, notes=[f"cheat_flags={session.cheat_flags + 1}"],
        )

    # 开场
    if not msg:
        content = PROBES[session.turn % len(PROBES)] if session.level == 0 else _hint(session.level, diag)
        return TutorDecision(action="hint", level=session.level, mode="probe",
                             content=content, p=diag.p, reason="opening")

    if is_meaningless(msg):
        return TutorDecision(action="hint", level=session.level, mode="probe",
                             content="我没太看懂，可以选一个：读入、计算、还是输出？",
                             p=diag.p, reason="clarify")

    # L3 的"两次尝试"闸门优先：不能让"我不会"直接绕过它
    if session.level >= 3 and session.failed_attempts < 2:
        if session.stuck_count >= 2:
            return TutorDecision(
                action="escalate", level=session.level, mode="explain",
                content="这一步需要更多证据，建议带上本次提交和运行记录，请老师一起核对。",
                p=diag.p, reason="stuck_at_gate",
            )
        return TutorDecision(
            action="hint", level=session.level, mode="gate",
            content="这一步请先动手核对本次失败用例，把程序实际输出和预期输出贴出来。"
                    "如果还没有失败输入，先确认本题有哪些可用测试。",
            p=diag.p, reason="stay",
        )

    # 关键分支：学生说不会
    if is_confused(msg):
        return _on_confused(session, diag, msg)

    # 正常回应

    if session.level >= MAX_LEVEL:
        return TutorDecision(action="escalate", level=MAX_LEVEL, mode="explain",
                             content="现有提示还不足以解决这道题，建议带上本次提交和证据，请老师一起核对。",
                             p=diag.p, reason="max_level")

    new_level = session.level + 1
    return TutorDecision(action="hint", level=new_level, mode="hint",
                         content=f"{_ack(msg)}\n\n{_hint(new_level, diag)}",
                         p=diag.p, reason="advance")


def _on_confused(session: SessionView, diag: DiagnosisView, msg: str) -> TutorDecision:
    """不会 ≠ 升级按钮。同一层反复不会就换讲法；到顶或反复三次则转人工。"""
    if session.level >= MAX_LEVEL:
        if session.stuck_count >= 1:
            return TutorDecision(
                action="escalate", level=MAX_LEVEL, mode="explain",
                content="现有提示还没有帮助你定位问题，建议带上本次提交和证据，请老师一起核对。",
                p=diag.p, reason="stuck_at_max",
            )
        return TutorDecision(action="explain", level=MAX_LEVEL, mode="walkthrough",
                             content=_explain(MAX_LEVEL, diag, deep=True),
                             p=diag.p, reason="walkthrough")

    if session.stuck_count >= 2:
        return TutorDecision(
            action="escalate", level=session.level, mode="explain",
            content="反复尝试仍未定位问题，建议把本次代码、输入和输出交给老师复核。",
            p=diag.p, reason="stuck_three_times",
        )

    if session.stuck_count >= 1:
        return TutorDecision(action="explain", level=session.level, mode="explain",
                             content=_explain(session.level, diag, deep=False),
                             p=diag.p, reason="explain_again")

    # 第一次说不会：给脚手架并前进一级（不要卡在原地）
    new_level = min(session.level + 1, MAX_LEVEL)
    return TutorDecision(action="hint", level=new_level, mode="explain",
                         content="没关系，卡住很正常。我换一种说法：\n\n" + _explain(new_level, diag, deep=False),
                         p=diag.p, reason="scaffold")


def _ack(msg: str) -> str:
    """接住学生原话，避免每轮都是同一句开头。"""
    if "不对" in msg or "试了" in msg or "还是" in msg:
        return "好，那我们再往下走一步。"
    for key, text in ACK.items():
        if key in msg:
            return text
    short = msg[:12] + ("…" if len(msg) > 12 else "")
    return f"收到，你提到「{short}」。"


def _hint(level: int, diag: DiagnosisView) -> str:
    loc = f"第 {diag.line} 行" + (f"第 {diag.col} 列" if diag.col else "") if diag.line else ""
    if level == 0:
        return "先别急着改，说说你打算怎么解决这道题？"
    if level == 1:
        if not diag.code or diag.hypothesis:
            return "现有证据还不能确定具体错因，先说明实际输出与预期输出哪里不同。"
        return f"方向提示：问题大概率在 **{_topic(diag)}** 这一类，不一定是思路全错。"
    if level == 2:
        if not loc:
            return "还没有定位到具体行。请手算本次失败输入，比较每一步的中间结果，找出最先不同的一步。"
        return f"定位提示：看看 {loc} 附近，那里和你的预期可能不一致。"
    if level == 3:
        if diag.counterexample:
            label = "已缩减的失败用例" if diag.counterexample_minimized else "本次失败用例（尚未最小化）"
            return (f"{label}：用下面的数据重跑你的代码，对照实际输出与预期输出：\n\n"
                    f"```\n{diag.counterexample.strip()}\n```\n\n先自己跑，别急着看答案。")
        return "还没有可用的失败输入。请先核对题目约束和实际运行记录，找到合法且确实失败的用例。"
    if not diag.code or diag.hypothesis:
        return "尚未证实具体根因。请根据已记录的输入输出差异，一次只修改一处并重新提交验证。"
    return f"修正提示：{diag.hint or '依据已定位的现象检查这一处，修改后重新提交验证。'}"


def _explain(level: int, diag: DiagnosisView, *, deep: bool) -> str:
    """比 _hint 更具体：讲清"为什么"，并给一个可操作的小实验。"""
    topic = _topic(diag)

    if level <= 1:
        if not diag.code or diag.hypothesis:
            return _hint(1, diag)
        return f"先看已有证据对应的方向：**{topic}**。请说明你原本期望程序做什么，再对照执行记录。"

    if level == 2:
        base = _hint(2, diag)
        if deep:
            base += "\n\n别一次改很多处，一次只改一处、改完立刻重新提交——这样才知道是哪一处生效。"
        return base

    if level == 3:
        return _hint(3, diag)

    base = (
        f"{_hint(4, diag)}\n"
        "先手算本次失败输入，再重新提交，确认修改是否解决了已观察到的问题。"
    )
    if deep:
        base += ("\n\n你也可以把改完的代码贴上来，我帮你看还有没有别的问题。"
                 "完整答案我不会直接给，但会陪你把这一段弄懂。")
    return base


def _topic(diag: DiagnosisView) -> str:
    mapping = {
        "P0": "输入法/标点（表层）",
        "P1": "语法规则",
        "P2": "实现细节（边界、类型、初始化）",
        "P3": "算法与复杂度",
        "P4": "题意理解",
        "P5": "做题策略",
    }
    return mapping.get(diag.p, "实现细节")


def _refuse_text(level: int) -> str:
    return (
        "我知道你赶时间，但直接给代码对你没有帮助——下次遇到同类题还是会卡。\n"
        f"我们只差一步：先按现在的提示（L{level}）试一次，卡住了再告诉我具体卡在哪一行。"
    )


def safe_output(text: str) -> bool:
    """防代做最后一道检查：不得包含完整程序。"""
    return isinstance(text, str) and not FULL_PROGRAM.search(text)
