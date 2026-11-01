"""教师审定：锁定当前版本，绑定本次证据并追加审计，不改机器判题。"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.input_types import ObjectId
from app.core.text_validation import StorageText
from app.domain.models import Diagnosis, DiagnosisReview, Evidence, User
from app.services.access import accessible_submission

EvidenceId = ObjectId


class ReviewMechanism(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^M(?:0[1-9]|1[01])$")
    explain: StorageText = Field(min_length=1, max_length=1000)
    evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=100)
    confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator("explain")
    @classmethod
    def require_explanation(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("机制解释不能为空")
        return value.strip()


class ReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    submission_id: ObjectId
    expected_revision: int = Field(ge=0)
    decision: Literal["confirm", "revise", "return"]
    narrative: StorageText = Field(min_length=6, max_length=4000)
    evidence_ids: list[EvidenceId] = Field(default_factory=list, max_length=100)
    mechanism: list[ReviewMechanism] | None = Field(default=None, max_length=11)

    @field_validator("narrative")
    @classmethod
    def require_narrative(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 6:
            raise ValueError("请填写至少六个字符的复核说明")
        return value


def diagnosis_snapshot(diagnosis: Diagnosis) -> dict:
    return deepcopy({
        "narrative": diagnosis.narrative,
        "mechanism": diagnosis.mechanism or [],
        "cognition": diagnosis.cognition or [],
        "nature": diagnosis.nature or [],
        "confidence": diagnosis.confidence,
        "status": diagnosis.status,
        "degraded": diagnosis.degraded or [],
        "model_meta": diagnosis.model_meta or {},
        "reviewer_id": diagnosis.reviewer_id,
        "review_revision": diagnosis.review_revision,
        "reviewed_at": diagnosis.reviewed_at.isoformat() if diagnosis.reviewed_at else None,
    })


def review_out(review: DiagnosisReview) -> dict:
    return {
        "id": review.id,
        "submission_id": review.submission_id,
        "revision": review.revision,
        "actor": {"id": review.actor_id, "name": review.actor_name},
        "decision": review.decision,
        "narrative": review.narrative,
        "evidence_ids": review.evidence_ids,
        "before": review.before,
        "after": review.after,
        "created_at": review.created_at.isoformat(),
    }


class ReviewService:
    def __init__(self, session: AsyncSession, user: User) -> None:
        self.session = session
        self.user = user

    async def create(self, payload: ReviewCreate) -> dict:
        if self.user.role not in ("teacher", "admin"):
            raise HTTPException(status_code=403, detail="只有教师或管理员可以审定诊断")
        submission = await accessible_submission(
            self.session, self.user, payload.submission_id, lock=True
        )
        if submission.stage != "READY":
            raise HTTPException(status_code=409, detail="提交尚未完成判题，不能审定")
        diagnosis = (
            await self.session.execute(
                select(Diagnosis).where(Diagnosis.submission_id == submission.id)
                .with_for_update(of=Diagnosis)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if diagnosis is None:
            raise HTTPException(status_code=409, detail="当前提交尚无诊断，请先生成诊断")
        if diagnosis.review_revision != payload.expected_revision:
            raise HTTPException(status_code=409, detail="诊断已更新，请刷新后依据当前版本复核")

        evidence_ids = list(dict.fromkeys(payload.evidence_ids))
        valid_ids = set((
            await self.session.execute(
                select(Evidence.id).where(Evidence.submission_id == submission.id)
            )
        ).scalars().all())
        if not set(evidence_ids).issubset(valid_ids):
            raise HTTPException(status_code=422, detail="证据不存在或不属于当前提交")
        if payload.decision != "return" and not evidence_ids:
            raise HTTPException(status_code=422, detail="确认或修订必须引用本次提交的真实证据")

        if payload.mechanism is not None:
            mechanism = [claim.model_dump(exclude_none=True) for claim in payload.mechanism]
        elif payload.decision == "confirm":
            mechanism = deepcopy(diagnosis.mechanism or [])
        else:
            mechanism = []
        for claim in mechanism:
            refs = claim.get("evidence_ids") or []
            if not refs or not set(refs).issubset(set(evidence_ids)):
                raise HTTPException(status_code=422, detail="每条机制解释必须引用本次选中的真实证据")

        before = diagnosis_snapshot(diagnosis)
        diagnosis.narrative = payload.narrative
        diagnosis.mechanism = mechanism
        # 一次代码审定不能替学生推断认知缺陷；修订时也不沿用旧定位。
        diagnosis.cognition = []
        if payload.decision != "confirm":
            diagnosis.nature = []
        diagnosis.status = "needs_human" if payload.decision == "return" else "confirmed"
        diagnosis.reviewer_id = self.user.id
        diagnosis.reviewed_at = datetime.now(UTC)
        diagnosis.review_revision += 1
        diagnosis.model_meta = {
            **(diagnosis.model_meta or {}),
            "route": "human" if payload.decision == "return" else "teacher_review",
            "review_decision": payload.decision,
            "reviewer_name": self.user.display_name or self.user.username,
            "review_evidence_ids": evidence_ids,
        }
        audit = DiagnosisReview(
            diagnosis_id=diagnosis.id, submission_id=submission.id,
            actor_id=self.user.id, actor_name=self.user.display_name or self.user.username,
            revision=diagnosis.review_revision, decision=payload.decision,
            narrative=payload.narrative, evidence_ids=evidence_ids,
            before=before, after=diagnosis_snapshot(diagnosis),
        )
        self.session.add(audit)
        await self.session.commit()
        return {
            "review": review_out(audit),
            "diagnosis": {
                "review_revision": diagnosis.review_revision,
                "status": diagnosis.status,
                "narrative": diagnosis.narrative,
                "reviewer_id": diagnosis.reviewer_id,
                "reviewed_at": diagnosis.reviewed_at.isoformat(),
            },
        }

    async def history(self, submission_id: int) -> dict:
        await accessible_submission(self.session, self.user, submission_id)
        diagnosis = (
            await self.session.execute(
                select(Diagnosis).where(Diagnosis.submission_id == submission_id)
                .with_for_update(read=True, of=Diagnosis)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        rows = (
            await self.session.execute(
                select(DiagnosisReview).where(DiagnosisReview.submission_id == submission_id)
                .order_by(DiagnosisReview.revision)
            )
        ).scalars().all()
        return {
            "items": [review_out(review) for review in rows],
            "current_revision": diagnosis.review_revision if diagnosis else 0,
        }
