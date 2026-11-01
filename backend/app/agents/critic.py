"""评审官：在"结论发给学生"之前做最后一道闸门。

它检查的不是"对不对"（那需要再跑一次沙箱），而是**结构与合规**：
1. 每条机制结论必须有证据引用，且引用真实存在
2. 不得输出可直接提交的完整代码（防代做）
3. 叙述不能为空、长度合理
4. 置信度与证据强度要匹配（P0 规则结论才允许 0.95+）
"""

from __future__ import annotations

from app.agents.contracts import Critique, DiagnosisDraft
from app.agents.tutor import safe_output


def review(draft: DiagnosisDraft, evidence_ids: set[int]) -> Critique:
    issues: list[str] = []

    # 1) 证据引用必须真实存在
    for claim in [*draft.mechanism, *draft.cognition]:
        missing = [i for i in claim.evidence_ids if i not in evidence_ids]
        if missing:
            issues.append(f"{claim.code} 引用了不存在的证据 {missing}")
        if not claim.evidence_ids:
            issues.append(f"{claim.code} 没有绑定任何证据")

    # 不依赖代码围栏；完整程序放在假设解释里也不能绕过检查。
    if not all(safe_output(text) for text in [draft.narrative, draft.hint_seed,
                                             *(claim.explain for claim in draft.mechanism)]):
        issues.append("narrative 里出现完整程序（防代做红线）")

    # 3) 叙述质量
    if len(draft.narrative.strip()) < 6:
        issues.append("narrative 过短，无法解释清楚")

    # 4) 置信度与证据强度匹配
    if draft.confidence >= 0.95 and not any(n.p in ("P0", "P1") for n in draft.nature):
        issues.append("没有确定性证据却给出 ≥0.95 置信度")

    blocking = [i for i in issues if "不存在" in i or "防代做" in i or "没有绑定" in i]
    return Critique(passed=not blocking, issues=issues,
                    severity="block" if blocking else "info")
