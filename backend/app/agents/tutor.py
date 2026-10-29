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
RESOLVED = re.compile(r"(改好了|过了|ac 了|ac了|通过了|成功了|对了)", re.I)
FULL_PROGRAM = re.compile(r"int\s+main\s*\(", re.I)
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
                content="这一步你自己试不出来也正常，我把情况同步给老师，让他陪你走一遍。",
                p=diag.p, reason="stuck_at_gate",
            )
        return TutorDecision(
            action="hint", level=session.level, mode="gate",
            content="我理解你想直接听讲解，但这一步需要你先动手：把 L3 那组数据跑一下，"
                    "把程序输出贴给我——哪怕只贴一个数，我也能接着往下讲。",
            p=diag.p, reason="stay",
        )

    # 关键分支：学生说不会
    if is_confused(msg):
        return _on_confused(session, diag, msg)

    # 正常回应

    if session.level >= MAX_LEVEL:
        return TutorDecision(action="escalate", level=MAX_LEVEL, mode="explain",
                             content="这道题我到顶了，剩下的让老师跟你一起过一遍。",
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
                content="看来我这样讲你还是接不上，我把这道题的情况同步给老师，"
                        "他会用另一种方式跟你过一遍。",
                p=diag.p, reason="stuck_at_max",
            )
        return TutorDecision(action="explain", level=MAX_LEVEL, mode="walkthrough",
                             content=_explain(MAX_LEVEL, diag, deep=True),
                             p=diag.p, reason="walkthrough")

    if session.stuck_count >= 2:
        return TutorDecision(
            action="escalate", level=session.level, mode="explain",
            content="这道题我们换个真人来带你——我把前面的情况都整理给老师了。",
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
    if not diag.code and level > 0:
        return (
            "现有证据还不能定位具体错因。"
            "先对照本次运行的实际输出与预期输出，说明最先不同的地方；"
            "如果只有样例通过，请补查题目允许的边界情况。"
        )
    loc = f"第 {diag.line} 行" + (f"第 {diag.col} 列" if diag.col else "") if diag.line else ""
    if level == 0:
        return "先别急着改，说说你打算怎么解决这道题？"
    if level == 1:
        return f"方向提示：问题大概率在 **{_topic(diag)}** 这一类，不一定是思路全错。"
    if level == 2:
        return f"定位提示：看看 {loc or '你写的核心那几行'} 附近，那里和你的预期可能不一致。"
    if level == 3:
        if diag.counterexample:
            return ("反例提示：用下面这组数据跑一下你的代码，看看输出对不对：\n\n"
                    f"```\n{diag.counterexample.strip()}\n```\n\n先自己跑，别急着看答案。")
        return "反例提示：试着自己造一组最小的数据（比如只有 1 个元素），看会不会出错。"
    return f"修正提示：{diag.hint or '把这一处的类型/边界检查补上，然后重新提交。'}"


def _explain(level: int, diag: DiagnosisView, *, deep: bool) -> str:
    """比 _hint 更具体：讲清"为什么"，并给一个可操作的小实验。"""
    topic = _topic(diag)
    loc = f"第 {diag.line} 行" if diag.line else "你写的那几行"

    if not diag.code:
        return _hint(level or 1, diag)

    if level <= 1:
        base = (
            f"我们先把这类错误说清楚：它属于 **{topic}**。\n"
            f"这类问题的特点是——代码能跑、样例可能也过，但换个数据就错。\n"
            f"所以先别怀疑整个思路，只需要在{loc}找一个小地方核对。"
        )
        if deep:
            base += ("\n\n举个例子：题目说 n 最多 10 万，而你的数组按 1 万开，"
                     "小样例一定看不出问题，改成大数据才会崩——这就是“样例会骗人”。")
        return base

    if level == 2:
        base = (
            f"具体到这个现象：{diag.message or '结果和预期不一致'}。\n"
            f"位置在 {loc}。先做一件事：把这行的变量值打印出来，和你手算的对比，"
            f"看是从第几步开始不一样。"
        )
        if deep:
            base += "\n\n别一次改很多处，一次只改一处、改完立刻重新提交——这样才知道是哪一处生效。"
        return base

    if level == 3:
        base = (
            "我们用一个最小例子把它逼出来：\n"
            "1）只留 1 个元素，看输出对不对；\n"
            "2）再加 1 个，观察从哪一步开始不对；\n"
            "3）把那一刻的中间变量打印出来。"
        )
        if diag.counterexample:
            base += f"\n\n也可以直接用这组数据：\n```\n{diag.counterexample.strip()}\n```"
        return base

    base = (
        f"最后一步，把改法说明白：{diag.hint or '检查这一处的类型与边界'}。\n"
        "改完先别急着交——拿题目里最小的那组数据手算一遍，再提交。"
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
    return not (FULL_PROGRAM.search(text) and "```" in text)
