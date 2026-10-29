"""诊断官：把证据翻译成"机制 + 性质 + 置信度"。

M4 阶段先做**确定性版本**（规则/模板），LLM 版本通过同一接口替换：
    diagnose(evidence, verdict) -> DiagnosisDraft
这样流程、闸门、评测都能先跑起来，也天然是"零成本基线"。
"""

from __future__ import annotations

from app.agents.contracts import CauseClaim, DiagnosisDraft, NatureTag
from app.capabilities.ce_classify import classify_compile, classify_runtime
from app.capabilities.triage import triage

HINT_LEVEL_BY_P = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 2, "P5": 3}


def _payload(ev: dict) -> dict:
    return ev.get("payload") or {}


def diagnose(evidence: list[dict], verdict: str | None, code: str = "") -> DiagnosisDraft:
    nature: list[NatureTag] = []
    mechanism: list[CauseClaim] = []
    cognition: list[CauseClaim] = []
    degraded: list[str] = []

    # --- 1) 表层扫描证据（P0，最高优先级）---
    for ev in evidence:
        if ev.get("type") != "static_alert":
            continue
        for f in _payload(ev).get("findings", []):
            nature.append(NatureTag(
                p=f.get("p", "P0"), code=f.get("code", ""), line=f.get("line"),
                col=f.get("col"), message=f.get("message", ""), hint=f.get("hint", ""),
            ))

    # --- 2) 编译日志（P1/P2）---
    for ev in evidence:
        if ev.get("type") != "compile_log":
            continue
        stderr = _payload(ev).get("stderr", "")
        if not stderr:
            continue
        for f in classify_compile(stderr).findings:
            nature.append(NatureTag(
                p=f.nature, code=f.code, line=f.line, col=f.col,
                message=f.message, hint=f.hint,
            ))
            if f.code.startswith("M"):
                _add_claim(mechanism, f.code, f.confidence, ev["id"], f.message)

    # --- 3) 运行证据（RE/TLE/MLE）---
    for ev in evidence:
        if ev.get("type") != "run_trace":
            continue
        p = _payload(ev)
        status = p.get("status")
        if status == "Accepted":
            continue
        kind = "TLE" if "Time" in str(status) else "MLE" if "Memory" in str(status) else "RE"
        for f in classify_runtime(p.get("stderr", ""), kind).findings:
            nature.append(NatureTag(p=f.nature, code=f.code, message=f.message, hint=f.hint))
            _add_claim(mechanism, f.code, f.confidence, ev["id"], f.message)

    # --- 4) 兜底：用 triage 的规则再扫一遍源码（防止证据不全）---
    if not nature and code:
        tr = triage(code, verdict=verdict)
        for f in tr.findings:
            nature.append(NatureTag(p=f.p, code=f.code, line=f.line, col=f.col,
                                    message=f.message, hint=f.hint))
        degraded.append("evidence_incomplete_fallback_to_triage")

    # 单次标点错误不能证明学习习惯或认知缺陷；暂不自动给学生贴认知标签。

    # --- 6) 汇总 ---
    conf = max((c.confidence for c in mechanism), default=0.0)
    if nature and conf == 0.0:
        # 规则命中即可确认：P0 是字符级确定判断，P1 是编译器的确定性输出
        if any(n.p == "P0" for n in nature):
            conf = 1.0
        elif any(n.p == "P1" for n in nature):
            conf = 0.8
        else:
            conf = 0.7

    worst = min(nature, key=lambda n: n.p, default=None) if nature else None
    hint_level = HINT_LEVEL_BY_P.get(worst.p, 1) if worst else 2

    narrative = _narrative(nature, verdict)
    need_human = not nature and verdict not in ("AC", "SAMPLE_PASS", "NO_TESTS", None)

    return DiagnosisDraft(
        mechanism=mechanism,
        cognition=cognition,
        nature=nature,
        narrative=narrative,
        confidence=conf,
        first_hint_level=hint_level,
        hint_seed=_hint_seed(worst),
        need_human=need_human,
        degraded=degraded,
    )


def _add_claim(bucket: list[CauseClaim], code: str, conf: float, ev_id: int, explain: str) -> None:
    for c in bucket:
        if c.code == code:
            if ev_id not in c.evidence_ids:
                c.evidence_ids.append(ev_id)
            return
    bucket.append(CauseClaim(code=code, confidence=conf, evidence_ids=[ev_id], explain=explain[:200]))


def _narrative(nature: list[NatureTag], verdict: str | None) -> str:
    if not nature:
        return {
            "AC": "已通过本题当前版本的正式测试。请复盘这次修改，单次通过不等于掌握知识点。",
            "SAMPLE_PASS": "公开样例已通过，但未运行正式测试，暂时不能确认程序正确。",
            "NO_TESTS": "代码已编译，但本题没有可用测试数据，结果尚未验证。",
        }.get(verdict, "现有证据不足以定位具体错因，请先查看失败输入与实际输出。")
    top = min(nature, key=lambda n: n.p)
    loc = f"第 {top.line} 行" + (f" 第 {top.col} 列" if top.col else "") if top.line else ""
    more = f"（共 {len(nature)} 处，先修这个）" if len(nature) > 1 else ""
    return f"{loc}：{top.message}{more} 建议：{top.hint}".strip()


def _hint_seed(top: NatureTag | None) -> str:
    if top is None:
        return ""
    if top.p == "P0":
        return "先看看这一行的标点，是不是中文输入法打出来的？"
    if top.p == "P1":
        return "编译器在提示语法问题，我们从报错位置往上一行看。"
    return "先别急着改，我们用一个更小的例子复现一下。"



# ---------------- LLM 兜底（只在规则判不了时使用）----------------

SYSTEM_PROMPT = """你是信息学竞赛教练，负责诊断学生代码的错误。

机制编码（mechanism.code 必须从下表选一个）：
M01 类型与溢出（int 装不下 / 乘法溢出 / signedness）
M02 数组与内存（越界 / 开小 / 多测未清空）
M03 初始化与未定义行为（未初始化 / 野指针 / 迭代器失效）
M04 边界与特判（n=1 / 空 / 全相同 / 负数）
M05 循环与终止（死循环 / 递归无终止 / 爆栈）
M06 复杂度（算法复杂度选错 / 常数过大 / IO 慢）
M07 取模与精度（负数取模 / 浮点误差）
M08 题意理解（读错格式 / 漏条件 / 输出要求）
M09 算法模型（贪心不成立 / DP 状态错 / 建模错）
M10 实现细节（比较器 / 二分边界 / 离散化）
M11 工程习惯（调试输出 / 文件读写）

规则：
1. 只输出 JSON，不要输出解释性文字。
2. 只能基于给定证据下结论，evidence_ids 必须从提供的证据编号里选。
3. 置信度不能超过 0.6（这是推断，不是确定性结论）。
4. 不要输出完整代码。
5. narrative 面向学生，一句话说清"错在哪 + 先看什么"，不要下命令式结论。

JSON 结构：
{"mechanism":[{"code":"M10","confidence":0.5,"evidence_ids":[1],"explain":"..."}],
 "narrative":"给学生看的一句话",
 "first_hint_level":2}"""


def _evidence_digest(evidence: list[dict]) -> str:
    lines = []
    for ev in evidence:
        p = _payload(ev)
        if ev.get("type") == "static_alert":
            lines.append(f"[证据 {ev['id']}] 表层扫描: {p.get('count')} 处 {p.get('findings', [])[:2]}")
        elif ev.get("type") == "compile_log":
            lines.append(f"[证据 {ev['id']}] 编译错误: {str(p.get('stderr', ''))[:300]}")
        elif ev.get("type") == "run_trace":
            lines.append(f"[证据 {ev['id']}] 运行: status={p.get('status')} "
                         f"stdout={str(p.get('stdout'))[:80]} expected={str(p.get('expected'))[:80]}")
    return "\n".join(lines)[:2000]


async def llm_hypothesis(draft: DiagnosisDraft, evidence: list[dict], verdict: str | None,
                         code: str, llm) -> DiagnosisDraft | None:
    """规则判不了时，让模型提假设——但结论仍必须绑定证据，置信度封顶 0.6。"""
    if llm is None or not getattr(llm, "available", False) or not evidence:
        return None

    user = (f"判题结果: {verdict}\n\n证据:\n{_evidence_digest(evidence)}\n\n"
            f"学生代码（截断）:\n```cpp\n{code[:2500]}\n```")
    try:
        data = await llm.chat_json(SYSTEM_PROMPT, user)
    except Exception:  # 模型服务失败不能掩盖已保存的判题证据。
        return None
    if not isinstance(data, dict):
        return None

    valid_ids = {ev["id"] for ev in evidence}
    mechanism: list[CauseClaim] = []
    items = data.get("mechanism")
    if not isinstance(items, list):
        return None
    for item in items[:3]:
        if not isinstance(item, dict):
            continue
        raw_ids = item.get("evidence_ids")
        if not isinstance(raw_ids, list):
            continue
        ids = [i for i in raw_ids if type(i) is int and i in valid_ids]
        if not ids:
            continue  # 禁止替模型随意补一个真实编号，伪造证据关联。
        claim_code = item.get("code")
        if claim_code not in {f"M{i:02d}" for i in range(1, 12)}:
            continue
        try:
            confidence = min(float(item.get("confidence", 0.5)), 0.6)
            if not 0 <= confidence <= 0.6:
                continue
        except (TypeError, ValueError):
            continue
        mechanism.append(CauseClaim(
            code=claim_code,
            confidence=confidence,
            evidence_ids=ids,
            explain=str(item.get("explain", ""))[:200],
        ))

    if not mechanism:
        return None

    narrative = str(data.get("narrative") or draft.narrative)[:500]
    return draft.model_copy(update={
        "mechanism": mechanism,
        "narrative": narrative or "这是一个推测性结论，已请老师确认。",
        "confidence": min(max(c.confidence for c in mechanism), 0.6),
        "need_human": True,
        "degraded": [*draft.degraded, "llm_hypothesis"],
    })
