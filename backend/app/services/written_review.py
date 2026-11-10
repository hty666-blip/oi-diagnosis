"""逐题修订与审核发布；原始采集记录始终只读。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import select

from app.domain.models import (
    WrittenCandidate,
    WrittenImportAsset,
    WrittenQuestion,
    WrittenQuestionVersion,
    WrittenReviewRevision,
)
from app.services.admin_service import audit
from app.services.written_bank_storage import load_bytes, load_payload, load_snapshot, save_payload

CRITICAL_FLAGS = {
    "answer_conflict",
    "withdrawn_question_requires_review",
    "possibly_out_of_syllabus",
    "code_in_image",
}


class OptionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=16, pattern=r"^[A-Za-z0-9_-]+$")
    text: str = Field(max_length=4000)


class QuestionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stem: str = Field(max_length=30000)
    shared_stem: str = Field(default="", max_length=60000)
    question_type: Literal["single_choice", "multiple_choice", "true_false", "fill_blank"]
    level: Literal["J", "S"] | None = None
    options: list[OptionInput] = Field(default_factory=list, max_length=26)
    answer: dict = Field(default_factory=dict)
    score: float = Field(default=0, strict=True, ge=0, le=100, allow_inf_nan=False)
    scoring_rule: Literal["all_or_nothing", "exact_text", "unconfirmed"] = "unconfirmed"
    primary_knowledge_point_id: str | None = Field(default=None, max_length=80)
    secondary_knowledge_point_ids: list[str] = Field(default_factory=list, max_length=8)
    explanation: str = Field(default="", max_length=12000)
    answer_reference: str = Field(default="", max_length=2000)


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(strict=True, ge=0)
    request_id: UUID
    action: Literal["save", "approve", "withdraw", "reopen"]
    note: str = Field(min_length=3, max_length=2000)
    draft: QuestionDraft | None = None
    rights_confirmed: StrictBool = False
    resolutions: dict[str, str] = Field(default_factory=dict, max_length=10)


def initial_draft(record: dict, group: dict | None) -> dict:
    return {
        "stem": record["stem"],
        "shared_stem": record.get("shared_stem") or (group or {}).get("shared_stem", ""),
        "question_type": record["question_type"],
        "level": record.get("level"),
        "options": record.get("options") or [],
        "answer": record.get("answer") or {},
        "score": record.get("score") or 0,
        "scoring_rule": record.get("scoring_rule")
        if record.get("scoring_rule") in {"all_or_nothing", "exact_text"}
        else "unconfirmed",
        "primary_knowledge_point_id": record.get("primary_knowledge_point_id"),
        "secondary_knowledge_point_ids": record.get("secondary_knowledge_point_ids", []),
        "explanation": record.get("explanation") or "",
        "answer_reference": "",
    }


def source_flags(candidate, group):
    return sorted(set(candidate.issues or []) | set((group or {}).get("issues") or []))


async def candidate_for(session, batch_id: str, external_id: str):
    return await session.scalar(
        select(WrittenCandidate)
        .where(WrittenCandidate.batch_id == batch_id, WrittenCandidate.external_id == external_id)
        .execution_options(populate_existing=True)
    )


async def publication_for(session, candidate_id):
    return await session.scalar(
        select(WrittenQuestion)
        .where(WrittenQuestion.candidate_id == candidate_id)
        .execution_options(populate_existing=True)
    )


def publication_out(question):
    return (
        {
            "id": question.id,
            "revision": question.revision,
            "catalog_version": question.catalog_version,
            "is_active": question.is_active,
        }
        if question
        else None
    )


async def review_state(session, batch, candidate, payload=None):
    payload = payload or await load_payload(session, batch)
    group = next(
        (g for g in payload["question_groups"] if g["id"] == candidate.record.get("group_id")), None
    )
    saved = (
        await load_snapshot(session, batch.id, candidate.draft_metadata)
        if candidate.draft_metadata
        else None
    )
    return {
        "revision": candidate.revision,
        "status": candidate.review_status,
        "draft": saved["draft"] if saved else initial_draft(candidate.record, group),
        "resolutions": saved.get("resolutions", {}) if saved else {},
        "source_flags": source_flags(candidate, group),
        "reviewed_by": candidate.reviewed_by,
        "reviewed_at": candidate.reviewed_at,
        "publication": publication_out(await publication_for(session, candidate.id)),
    }


def validate_approval(
    draft: dict, request: ReviewInput, flags: list[str], nodes: list[dict], assets: list[str]
):
    if not request.rights_confirmed:
        raise HTTPException(422, "请确认本题题面、图片和解析可在本系统用于教学")
    if not draft["stem"].strip() or draft["level"] not in {"J", "S"} or draft["score"] <= 0:
        raise HTTPException(422, "发布前请补齐题干、J/S 级别和正数分值")
    if len(draft["explanation"].strip()) < 8 or len(draft["answer_reference"].strip()) < 5:
        raise HTTPException(422, "发布前请补充解析和答案核对依据")
    leaves = {n["id"] for n in nodes if n.get("is_leaf") is True}
    primary, secondary = draft["primary_knowledge_point_id"], draft["secondary_knowledge_point_ids"]
    if (
        primary not in leaves
        or len(secondary) != len(set(secondary))
        or primary in secondary
        or any(n not in leaves for n in secondary)
    ):
        raise HTTPException(422, "主次知识点须选择存在的叶子知识点，且不能重复")
    for flag in set(flags) & CRITICAL_FLAGS:
        if len(request.resolutions.get(flag, "").strip()) < 8:
            raise HTTPException(422, "请逐项说明答案冲突、撤题、范围或图片程序等重要问题的处理依据")
    kind, answer, options = draft["question_type"], draft["answer"], draft["options"]
    ids = [o["id"] for o in options]
    if kind in {"single_choice", "multiple_choice"}:
        chosen = answer.get("option_ids")
        if (
            set(answer) != {"kind", "option_ids"}
            or answer.get("kind") != ("option" if kind == "single_choice" else "options")
            or not isinstance(chosen, list)
            or not chosen
            or any(not isinstance(v, str) for v in chosen)
            or len(chosen) != len(set(chosen))
            or not set(chosen).issubset(ids)
            or len(ids) < 2
            or len(ids) != len(set(ids))
            or any(not o["text"].strip() for o in options)
            or kind == "single_choice"
            and len(chosen) != 1
        ):
            raise HTTPException(422, "选择题选项及答案不完整或引用无效")
    elif kind == "true_false":
        if (
            set(answer) != {"kind", "value"}
            or answer.get("kind") != "boolean"
            or type(answer.get("value")) is not bool
        ):
            raise HTTPException(422, "判断题答案须明确选择正确或错误")
    else:
        accepted = answer.get("accepted")
        if (
            set(answer) != {"kind", "accepted", "normalization"}
            or answer.get("kind") != "text"
            or not isinstance(accepted, list)
            or not 1 <= len(accepted) <= 20
            or any(not isinstance(v, str) or not v.strip() or len(v) > 4000 for v in accepted)
            or answer.get("normalization") not in {"exact", "trim_outer_whitespace"}
        ):
            raise HTTPException(422, "填空题须配置有效答案及明确的空白比较规则")
    expected_rule = "exact_text" if kind == "fill_blank" else "all_or_nothing"
    if draft["scoring_rule"] != expected_rule:
        raise HTTPException(422, "计分规则待确认：本批支持全对得分，填空按指定文本规则比较")
    # 图片仅允许沿用这一题及其题组的固定资源。
    material = "\n".join([draft["stem"], draft["shared_stem"], *(o["text"] for o in options)])
    for path in re.findall(r"!\[[^\]]*\]\(([^)\n]+)\)", material):
        if path not in assets:
            raise HTTPException(422, "审核题面引用了不属于本题的图片")


async def apply_review(session, batch, candidate, user, request: ReviewInput):
    if len(request.note.strip()) < 3 or any(
        k not in CRITICAL_FLAGS or not isinstance(v, str) or len(v) > 2000
        for k, v in request.resolutions.items()
    ):
        raise HTTPException(422, "请填写有效的审核说明与问题处理依据")
    serialized = json.dumps(
        request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, allow_nan=False
    )
    if len(serialized.encode()) > 512 * 1024:
        raise HTTPException(413, "单题审核内容不得超过 512 KiB")
    digest = hashlib.sha256(serialized.encode()).hexdigest()
    previous = await session.scalar(
        select(WrittenReviewRevision).where(
            WrittenReviewRevision.candidate_id == candidate.id,
            WrittenReviewRevision.request_id == str(request.request_id),
        )
    )
    if previous:
        if previous.actor_id != user.id or previous.request_digest != digest:
            raise HTTPException(409, "该请求编号已经用于其他审核内容")
        return True, previous.revision
    if candidate.revision != request.expected_revision:
        raise HTTPException(409, "题目已被其他操作修改，请保留输入并重新打开最新版本")
    if candidate.review_status == "withdrawn" and request.action not in {"withdraw", "reopen"}:
        raise HTTPException(409, "题目已撤下，请先恢复待审核状态")
    if request.action in {"save", "approve"} and request.draft is None:
        raise HTTPException(422, "请提供修订稿")
    payload = await load_payload(session, batch)
    state = await review_state(session, batch, candidate, payload)
    draft = request.draft.model_dump(mode="json") if request.draft else state["draft"]
    group = next(
        (g for g in payload["question_groups"] if g["id"] == candidate.record.get("group_id")), None
    )
    assets = list(
        dict.fromkeys(
            [*candidate.record.get("asset_paths", []), *(group or {}).get("asset_paths", [])]
        )
    )
    if request.action == "approve":
        validate_approval(
            draft, request, state["source_flags"], payload["knowledge_points"]["nodes"], assets
        )
        for path in assets:
            asset = await session.scalar(
                select(WrittenImportAsset).where(
                    WrittenImportAsset.batch_id == batch.id, WrittenImportAsset.path == path
                )
            )
            if asset is None:
                raise HTTPException(409, "本题图片缺失，请先恢复题包资源")
            await load_bytes(session, batch.id, path, asset.digest)
    before = {"revision": candidate.revision, "status": candidate.review_status}
    candidate.revision += 1
    candidate.review_status = (
        "approved"
        if request.action == "approve"
        else "withdrawn"
        if request.action == "withdraw"
        else "needs_review"
    )
    candidate.reviewed_by = user.id if request.action == "approve" else None
    candidate.search_text = draft["stem"]
    candidate.question_type = draft["question_type"]
    candidate.level = draft["level"]
    candidate.reviewed_at = datetime.now(UTC) if request.action == "approve" else None
    event_id = str(uuid4())
    saved = {
        "draft": draft,
        "resolutions": request.resolutions,
        "rights_confirmed": request.rights_confirmed,
        "note": request.note.strip(),
        "status": candidate.review_status,
        "action": request.action,
    }
    candidate.draft_metadata = save_payload(session, batch.id, saved, f"review:{event_id}")
    session.add(
        WrittenReviewRevision(
            id=event_id,
            candidate_id=candidate.id,
            revision=candidate.revision,
            request_id=str(request.request_id),
            request_digest=digest,
            actor_id=user.id,
            action=request.action,
            note=request.note.strip(),
            snapshot_metadata=candidate.draft_metadata,
        )
    )
    question = await publication_for(session, candidate.id)
    if request.action == "approve":
        if question is None:
            question = WrittenQuestion(
                candidate_id=candidate.id,
                revision=0,
                catalog_version=0,
                is_active=False,
                public_metadata={},
            )
            session.add(question)
            await session.flush()
        question.revision += 1
        snapshot = {
            **draft,
            "asset_paths": assets,
            "source_record_id": candidate.external_id,
            "paper_id": candidate.paper_id,
            "source_locator": candidate.record.get("source_locator"),
            "review_revision": candidate.revision,
            "reviewed_by": user.id,
            "review_note": request.note.strip(),
            "resolutions": request.resolutions,
        }
        version_id = str(uuid4())
        metadata = save_payload(session, batch.id, snapshot, f"version:{version_id}")
        session.add(
            WrittenQuestionVersion(
                id=version_id,
                question_id=question.id,
                batch_id=batch.id,
                revision=question.revision,
                review_revision=candidate.revision,
                snapshot_metadata=metadata,
                actor_id=user.id,
            )
        )
        question.public_metadata = {
            "stem": draft["stem"][:160],
            "question_type": draft["question_type"],
            "level": draft["level"],
            "score": draft["score"],
        }
        question.is_active = True
    elif question is not None:
        question.is_active = False
    if question is not None:
        question.catalog_version += 1
    audit(
        session,
        user,
        f"written.{request.action}",
        f"{batch.id}:{candidate.external_id}",
        before,
        {
            "revision": candidate.revision,
            "status": candidate.review_status,
            "question_id": question.id if question else None,
        },
    )
    await session.flush()
    return False, candidate.revision
