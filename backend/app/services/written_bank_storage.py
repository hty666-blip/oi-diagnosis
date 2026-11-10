"""候选快照和图片以小行保存，避免驱动对大单行包的读取限制。"""

import base64
import hashlib
import json
import zlib

from fastapi import HTTPException
from sqlalchemy import select

from app.domain.models import WrittenImportChunk
from app.services.written_bank_package import MAX_EXPANDED

CHUNK_SIZE = 24 * 1024


def save_bytes(session, batch_id: str, key: str, data: bytes):
    encoded = base64.b64encode(data).decode("ascii")
    for position, offset in enumerate(range(0, len(encoded), CHUNK_SIZE)):
        session.add(
            WrittenImportChunk(
                batch_id=batch_id,
                key=key,
                position=position,
                content=encoded[offset : offset + CHUNK_SIZE],
            )
        )


async def load_bytes(session, batch_id: str, key: str, expected_digest: str) -> bytes:
    rows = (
        await session.scalars(
            select(WrittenImportChunk)
            .where(WrittenImportChunk.batch_id == batch_id, WrittenImportChunk.key == key)
            .order_by(WrittenImportChunk.position)
        )
    ).all()
    try:
        if not rows or [r.position for r in rows] != list(range(len(rows))):
            raise ValueError("missing_chunk")
        encoded = "".join(r.content for r in rows)
        if len(encoded) > MAX_EXPANDED * 2:
            raise ValueError("oversized_snapshot")
        value = base64.b64decode(encoded, validate=True)
        if hashlib.sha256(value).hexdigest() != expected_digest:
            raise ValueError("snapshot_digest")
        return value
    except ValueError as exc:
        raise HTTPException(409, "候选快照校验失败，请重新导入") from exc


def save_payload(session, batch_id: str, payload: dict) -> dict:
    compressed = zlib.compress(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    save_bytes(session, batch_id, "payload", compressed)
    return {"codec": "zlib-json-v1", "digest": hashlib.sha256(compressed).hexdigest()}


async def load_payload(session, batch) -> dict:
    metadata = batch.payload
    if metadata.get("codec") != "zlib-json-v1" or not isinstance(metadata.get("digest"), str):
        raise HTTPException(409, "候选快照格式不可用，请重新导入")
    compressed = await load_bytes(session, batch.id, "payload", metadata["digest"])
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, MAX_EXPANDED + 1)
        if len(raw) > MAX_EXPANDED or not decoder.eof or decoder.unused_data:
            raise ValueError("payload_limit")
        return json.loads(raw)
    except (ValueError, zlib.error) as exc:
        raise HTTPException(409, "候选快照无法恢复，请重新导入") from exc
