"""BKT 掌握度：小数据也能用、可解释、可回写。

p(L|obs) 用标准 BKT 后验，再加一次学习转移。
不确定度随观测次数下降，用来表达"这个估计有多可信"。
"""

from __future__ import annotations

from dataclasses import dataclass

P_LEARN = 0.15
P_SLIP = 0.10
P_GUESS = 0.20


@dataclass
class MasteryState:
    value: float = 0.3
    uncertainty: float = 0.5
    observations: int = 0

    def as_dict(self) -> dict:
        return {"value": round(self.value, 4), "uncertainty": round(self.uncertainty, 4),
                "observations": self.observations}


def update(state: MasteryState, correct: bool,
           p_learn: float = P_LEARN, p_slip: float = P_SLIP,
           p_guess: float = P_GUESS) -> MasteryState:
    p = min(max(state.value, 0.01), 0.99)
    if correct:
        num = (1 - p_slip) * p
        den = num + p_guess * (1 - p)
    else:
        num = p_slip * p
        den = num + (1 - p_guess) * (1 - p)
    posterior = num / den if den else p

    learned = posterior + (1 - posterior) * p_learn
    n = state.observations + 1
    # 观测越多，不确定度越低（最快到 0.15 左右）
    uncertainty = max(0.15, 0.5 * (0.85 ** n))
    return MasteryState(value=min(max(learned, 0.01), 0.99), uncertainty=uncertainty, observations=n)


def needs_review(state: MasteryState) -> bool:
    """掌握度低且已经比较确定 → 值得复习。"""
    return state.value < 0.5 and state.uncertainty < 0.35
