"""Agent 之间的契约（Pydantic）：Schema 即约束，不靠提示词自觉。

核心红线（架构 §6.4）：
- 机制层结论必须绑定 evidence_id，否则不允许构造出合法对象
- 提示层级不得跳级（由 Tutor 状态机再校验一次）
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

P_LEVELS = {"P0", "P1", "P2", "P3", "P4", "P5"}


class NatureTag(BaseModel):
    p: str
    code: str
    line: int | None = None
    col: int | None = None
    message: str = ""
    hint: str = ""

    @field_validator("p")
    @classmethod
    def _p_valid(cls, v: str) -> str:
        if v not in P_LEVELS:
            raise ValueError(f"非法 P 档位: {v}")
        return v


class CauseClaim(BaseModel):
    code: str = Field(..., description="M01..M11 / C1..C6")
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[int] = Field(default_factory=list)
    explain: str = ""

    @model_validator(mode="after")
    def _must_have_evidence(self) -> CauseClaim:
        if not self.evidence_ids:
            raise ValueError(f"结论 {self.code} 没有绑定证据，禁止 confirmed")
        return self


class DiagnosisDraft(BaseModel):
    """诊断官输出。"""

    mechanism: list[CauseClaim] = Field(default_factory=list)
    cognition: list[CauseClaim] = Field(default_factory=list)
    nature: list[NatureTag] = Field(default_factory=list)
    narrative: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    first_hint_level: int = Field(default=0, ge=0, le=4)
    hint_seed: str = ""
    need_human: bool = False
    degraded: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _narrative_required(self) -> DiagnosisDraft:
        if not self.narrative.strip():
            raise ValueError("narrative 不能为空")
        return self


class Critique(BaseModel):
    passed: bool
    issues: list[str] = Field(default_factory=list)
    severity: str = "info"  # info | block


class DiagnosisResult(BaseModel):
    status: str            # confirmed | needs_human
    route: str             # fast_fix | syntax_help | full_pipeline | human
    draft: DiagnosisDraft
    attempts: int = 0
    critique: Critique | None = None
