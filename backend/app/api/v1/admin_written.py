"""管理员笔试题库：固定导入、逐题修订、人工审核与版本发布。"""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import defer

from app.api.deps import AdminUser, SessionDep
from app.api.input_types import ObjectPathId
from app.domain.models import (
    WrittenCandidate,
    WrittenImportAsset,
    WrittenImportBatch,
    WrittenQuestion,
    WrittenQuestionVersion,
    WrittenReviewRevision,
)
from app.services.admin_service import admin_write, audit
from app.services.written_bank_package import MAX_UPLOAD, PackageError, parse_package
from app.services.written_bank_storage import (
    load_bytes,
    load_payload,
    load_snapshot,
    save_bytes,
    save_payload,
)
from app.services.written_review import (
    ReviewInput,
    apply_review,
    candidate_for,
    publication_out,
    review_state,
)

router = APIRouter(prefix="/admin/written-bank", tags=["admin-written-bank"])


def batch_out(batch, stats=None):
    return {
        "id": batch.id,
        "status": batch.status,
        "summary": {**batch.summary, **(stats or {})},
        "created_at": batch.created_at,
        "expires_at": batch.expires_at,
        "staged_at": batch.staged_at,
    }


async def batch_stats(session, batch_ids):
    rows = (
        await session.execute(
            select(
                WrittenCandidate.batch_id,
                func.sum(case((WrittenCandidate.review_status == "approved", 1), else_=0)),
                func.sum(case((WrittenCandidate.review_status == "withdrawn", 1), else_=0)),
                func.sum(case((WrittenQuestion.is_active.is_(True), 1), else_=0)),
            )
            .outerjoin(WrittenQuestion, WrittenQuestion.candidate_id == WrittenCandidate.id)
            .where(WrittenCandidate.batch_id.in_(batch_ids))
            .group_by(WrittenCandidate.batch_id)
        )
    ).all()
    return {
        bid: {"approved_count": int(a), "withdrawn_count": int(w), "published_count": int(p)}
        for bid, a, w, p in rows
    }


async def get_batch(session, user, batch_id):
    batch = await session.get(WrittenImportBatch, str(batch_id))
    if not batch or batch.status == "preview" and batch.actor_id != user.id:
        raise HTTPException(404, "导入批次不存在")
    if batch.status == "preview" and batch.expires_at < datetime.now(UTC):
        raise HTTPException(410, "预览已过期，请重新上传题包")
    return batch


@router.get("/batches")
async def batches(session: SessionDep, user: AdminUser):
    rows = (
        await session.scalars(
            select(WrittenImportBatch)
            .options(defer(WrittenImportBatch.payload))
            .where(
                (WrittenImportBatch.status == "staged")
                | (
                    (WrittenImportBatch.actor_id == user.id)
                    & (WrittenImportBatch.expires_at >= datetime.now(UTC))
                )
            )
            .order_by(WrittenImportBatch.created_at.desc(), WrittenImportBatch.id)
            .limit(51)
        )
    ).all()
    stats = await batch_stats(session, [b.id for b in rows[:50]])
    return {"items": [batch_out(b, stats.get(b.id)) for b in rows[:50]], "has_more": len(rows) > 50}


@router.post("/imports/preview")
async def preview(request: Request, session: SessionDep, user: AdminUser):
    if request.headers.get("content-type", "").split(";", 1)[0] not in {
        "application/zip",
        "application/octet-stream",
        "application/x-zip-compressed",
    }:
        raise HTTPException(415, "请选择 ZIP 题包")
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_UPLOAD:
            raise HTTPException(413, "ZIP 题包不得超过 16 MB")
        content.extend(chunk)
    try:
        payload, summary, assets = await asyncio.to_thread(parse_package, bytes(content))
    except (PackageError, ValueError, TypeError, KeyError, RecursionError) as exc:
        message = (
            str(exc) if isinstance(exc, PackageError) else "题包字段结构无效，请按交换格式核对"
        )
        raise HTTPException(422, message) from exc
    digest = hashlib.sha256(content).hexdigest()
    await admin_write(session, user, request)
    # 过期预览仅是可重新生成的临时快照；已暂存批次永久保留。
    await session.execute(
        delete(WrittenImportBatch).where(
            WrittenImportBatch.status == "preview",
            WrittenImportBatch.expires_at < datetime.now(UTC),
        )
    )
    existing = await session.scalar(
        select(WrittenImportBatch).where(
            WrittenImportBatch.actor_id == user.id, WrittenImportBatch.digest == digest
        )
    )
    if existing:
        await session.commit()
        return {**batch_out(existing), "replayed": True}
    batch = WrittenImportBatch(
        id=str(uuid4()),
        actor_id=user.id,
        digest=digest,
        status="preview",
        summary=summary,
        payload={},
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    session.add(batch)
    await session.flush()
    batch.payload = save_payload(session, batch.id, payload)
    for path, (data, media_type) in assets.items():
        session.add(
            WrittenImportAsset(
                batch_id=batch.id,
                path=path,
                media_type=media_type,
                digest=hashlib.sha256(data).hexdigest(),
            )
        )
        save_bytes(session, batch.id, path, data)
    audit(
        session,
        user,
        "written.preview",
        batch.id,
        {},
        {"digest": digest, "questions": summary["questions"]},
    )
    await session.commit()
    return {**batch_out(batch), "replayed": False}


@router.post("/batches/{batch_id}/stage")
async def stage(batch_id: UUID, request: Request, session: SessionDep, user: AdminUser):
    await admin_write(session, user, request)
    batch = await get_batch(session, user, batch_id)
    if batch.status == "staged":
        await session.commit()
        return {**batch_out(batch), "replayed": True}
    payload = await load_payload(session, batch)
    for q in payload["questions"]:
        review = payload["reviews"][q["id"]]
        session.add(
            WrittenCandidate(
                batch_id=batch.id,
                external_id=q["id"],
                paper_id=q["paper_id"],
                question_type=q["question_type"],
                level=q.get("level"),
                review_status="needs_review",
                search_text=q["stem"],
                record=q,
                normalized=review,
                issues=review["issues"],
            )
        )
    batch.status = "staged"
    batch.staged_at = datetime.now(UTC)
    audit(
        session,
        user,
        "written.stage",
        batch.id,
        {"status": "preview"},
        {"status": "staged", "questions": batch.summary["questions"], "published": 0},
    )
    await session.commit()
    return {**batch_out(batch), "replayed": False}


def candidate_out(record, normalized):
    return {
        "external_id": record["id"],
        "paper_id": record["paper_id"],
        "question_type": record["question_type"],
        "level": record.get("level"),
        "stem": record["stem"][:160],
        "review_status": "needs_review",
        "answer_status": record.get("answer_status"),
        "issues": normalized["issues"],
    }


@router.get("/batches/{batch_id}/questions")
async def questions(
    batch_id: UUID,
    session: SessionDep,
    user: AdminUser,
    offset: int = Query(0, ge=0, le=100000),
    q: str = Query("", max_length=120),
    paper_id: str = Query("", max_length=80),
    flagged: bool = False,
    state: str = Query("all", pattern="^(all|needs_review|approved|withdrawn)$"),
):
    batch = await get_batch(session, user, batch_id)
    # 有界快照使预览和暂存页面口径相同；正文分页输出，避免整包返回给浏览器。
    payload = await load_payload(session, batch)
    if batch.status == "staged":
        where = [WrittenCandidate.batch_id == batch.id]
        if q:
            where.append(
                WrittenCandidate.search_text.icontains(q, autoescape=True)
                | WrittenCandidate.external_id.icontains(q, autoescape=True)
            )
        if paper_id:
            where.append(WrittenCandidate.paper_id == paper_id)
        if flagged:
            where.append(func.json_length(WrittenCandidate.issues) > 0)
        if state != "all":
            where.append(WrittenCandidate.review_status == state)
        total = await session.scalar(
            select(func.count()).select_from(WrittenCandidate).where(*where)
        )
        candidates = (
            await session.scalars(
                select(WrittenCandidate)
                .where(*where)
                .order_by(WrittenCandidate.id)
                .offset(offset)
                .limit(25)
            )
        ).all()
        stats = await batch_stats(session, [batch.id])
        items = [
            {
                **candidate_out(c.record, c.normalized),
                "stem": c.search_text[:160],
                "review_status": c.review_status,
                "question_type": c.question_type,
                "level": c.level,
                "revision": c.revision,
            }
            for c in candidates
        ]
        return {
            "batch": batch_out(batch, stats.get(batch.id)),
            "items": items,
            "total": total,
            "has_more": total > offset + 25,
            "papers": [
                {"id": p["id"], "title": p.get("title", p["id"])} for p in payload["papers"]
            ],
        }
    records = payload["questions"]
    reviews = payload["reviews"]
    filtered = [
        r
        for r in records
        if (not q or q.casefold() in (r["stem"] + r["id"]).casefold())
        and (not paper_id or r["paper_id"] == paper_id)
        and (not flagged or bool(reviews[r["id"]]["issues"]))
    ]
    return {
        "batch": batch_out(batch),
        "items": [candidate_out(r, reviews[r["id"]]) for r in filtered[offset : offset + 25]],
        "total": len(filtered),
        "has_more": len(filtered) > offset + 25,
        "papers": [{"id": p["id"], "title": p.get("title", p["id"])} for p in payload["papers"]],
    }


@router.get("/batches/{batch_id}/questions/{question_id}")
async def question(batch_id: UUID, question_id: str, session: SessionDep, user: AdminUser):
    batch = await get_batch(session, user, batch_id)
    return await question_detail(session, batch, question_id)


async def question_detail(session, batch, question_id):
    payload = await load_payload(session, batch)
    record = next((q for q in payload["questions"] if q["id"] == question_id), None)
    if not record:
        raise HTTPException(404, "候选题不存在")
    group = next((g for g in payload["question_groups"] if g["id"] == record.get("group_id")), None)
    candidate = (
        await candidate_for(session, batch.id, question_id) if batch.status == "staged" else None
    )
    return {
        "record": record,
        "normalized": payload["reviews"][question_id],
        "group": group,
        "sources": payload["sources"],
        "knowledge_points": payload["knowledge_points"]["nodes"],
        "review": await review_state(session, batch, candidate, payload) if candidate else None,
    }


@router.post("/batches/{batch_id}/questions/{question_id}/review")
async def review_question(
    batch_id: UUID,
    question_id: str,
    body: ReviewInput,
    request: Request,
    session: SessionDep,
    user: AdminUser,
):
    await admin_write(session, user, request)
    batch = await get_batch(session, user, batch_id)
    candidate = await candidate_for(session, batch.id, question_id)
    if batch.status != "staged" or not candidate:
        raise HTTPException(409, "请先暂存题包，再开始逐题审核")
    replayed, applied = await apply_review(session, batch, candidate, user, body)
    await session.commit()
    return {
        **await question_detail(session, batch, question_id),
        "replayed": replayed,
        "applied_revision": applied,
    }


@router.get("/batches/{batch_id}/questions/{question_id}/history")
async def review_history(
    batch_id: UUID,
    question_id: str,
    session: SessionDep,
    user: AdminUser,
    before: int = Query(2**31 - 1, ge=1),
):
    batch = await get_batch(session, user, batch_id)
    candidate = await candidate_for(session, batch.id, question_id)
    if not candidate:
        raise HTTPException(404, "候选题尚未暂存")
    rows = (
        await session.scalars(
            select(WrittenReviewRevision)
            .where(
                WrittenReviewRevision.candidate_id == candidate.id,
                WrittenReviewRevision.revision < before,
            )
            .order_by(WrittenReviewRevision.revision.desc())
            .limit(21)
        )
    ).all()
    return {
        "items": [
            {
                "revision": r.revision,
                "actor_id": r.actor_id,
                "action": r.action,
                "note": r.note,
                "created_at": r.created_at,
            }
            for r in rows[:20]
        ],
        "has_more": len(rows) > 20,
    }


@router.get("/batches/{batch_id}/questions/{question_id}/history/{revision}")
async def historical_review(
    batch_id: UUID, question_id: str, revision: ObjectPathId, session: SessionDep, user: AdminUser
):
    batch = await get_batch(session, user, batch_id)
    candidate = await candidate_for(session, batch.id, question_id)
    if not candidate:
        raise HTTPException(404, "候选题尚未暂存")
    row = await session.scalar(
        select(WrittenReviewRevision).where(
            WrittenReviewRevision.candidate_id == candidate.id,
            WrittenReviewRevision.revision == revision,
        )
    )
    if not row:
        raise HTTPException(404, "修订记录不存在")
    return {
        "revision": row.revision,
        "action": row.action,
        "actor_id": row.actor_id,
        "note": row.note,
        "snapshot": await load_snapshot(session, batch.id, row.snapshot_metadata),
    }


class PublicationStateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(strict=True, ge=0)
    is_active: StrictBool


@router.post("/published/{question_id}/state")
async def publication_state(
    question_id: ObjectPathId,
    body: PublicationStateInput,
    request: Request,
    session: SessionDep,
    user: AdminUser,
):
    await admin_write(session, user, request)
    row = await session.get(WrittenQuestion, question_id, populate_existing=True)
    if not row:
        raise HTTPException(404, "发布题目不存在")
    if row.catalog_version != body.expected_version:
        raise HTTPException(409, "上下架状态已变化，请刷新题目")
    if body.is_active:
        candidate = await session.get(WrittenCandidate, row.candidate_id, populate_existing=True)
        version = await session.scalar(
            select(WrittenQuestionVersion).where(
                WrittenQuestionVersion.question_id == row.id,
                WrittenQuestionVersion.revision == row.revision,
            )
        )
        if (
            candidate.review_status != "approved"
            or version is None
            or version.review_revision != candidate.revision
        ):
            raise HTTPException(409, "最新修订尚未审核，不能上架旧版本")
    row.is_active = body.is_active
    row.catalog_version += 1
    audit(
        session,
        user,
        "written.state",
        str(row.id),
        {"catalog_version": body.expected_version},
        {"is_active": row.is_active, "catalog_version": row.catalog_version},
    )
    await session.commit()
    return publication_out(row)


@router.get("/batches/{batch_id}/asset")
async def asset(
    batch_id: UUID, session: SessionDep, user: AdminUser, path: str = Query(max_length=240)
):
    batch = await get_batch(session, user, batch_id)
    item = await session.scalar(
        select(WrittenImportAsset).where(
            WrittenImportAsset.batch_id == batch.id, WrittenImportAsset.path == path
        )
    )
    if not item:
        raise HTTPException(404, "图片不存在")
    data = await load_bytes(session, batch.id, path, item.digest)
    return Response(
        data,
        media_type=item.media_type,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
