"""管理员笔试候选导入：固定预览，幂等暂存，不提供正式发布接口。"""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import defer

from app.api.deps import AdminUser, SessionDep
from app.domain.models import WrittenCandidate, WrittenImportAsset, WrittenImportBatch
from app.services.admin_service import admin_write, audit
from app.services.written_bank_package import MAX_UPLOAD, PackageError, parse_package
from app.services.written_bank_storage import load_bytes, load_payload, save_bytes, save_payload

router = APIRouter(prefix="/admin/written-bank", tags=["admin-written-bank"])


def batch_out(batch):
    return {
        "id": batch.id,
        "status": batch.status,
        "summary": batch.summary,
        "created_at": batch.created_at,
        "expires_at": batch.expires_at,
        "staged_at": batch.staged_at,
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
    return {"items": [batch_out(b) for b in rows[:50]], "has_more": len(rows) > 50}


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
):
    batch = await get_batch(session, user, batch_id)
    # 有界快照使预览和暂存页面口径相同；正文分页输出，避免整包返回给浏览器。
    payload = await load_payload(session, batch)
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
    payload = await load_payload(session, batch)
    record = next((q for q in payload["questions"] if q["id"] == question_id), None)
    if not record:
        raise HTTPException(404, "候选题不存在")
    group = next((g for g in payload["question_groups"] if g["id"] == record.get("group_id")), None)
    return {
        "record": record,
        "normalized": payload["reviews"][question_id],
        "group": group,
        "sources": payload["sources"],
        "knowledge_points": payload["knowledge_points"]["nodes"],
    }


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
