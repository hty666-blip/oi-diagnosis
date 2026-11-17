"""按原试卷组织当前可作答的已审核题目。"""

from collections import Counter
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, Response
from sqlalchemy import func

from app.api.deps import CurrentUser, SessionDep
from app.api.v1.written_questions import summary
from app.domain.models import (
    WrittenCandidate,
    WrittenImportBatch,
    WrittenQuestion,
    WrittenQuestionVersion,
)
from app.services.written_bank_storage import load_payload
from app.services.written_review import visible_query

router = APIRouter(prefix="/written/papers", tags=["written-papers"])
PAGE_SIZE = 25
PaperId = Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")]
PageOffset = Annotated[int, Query(ge=0, le=100000)]


def paper_out(batch, paper, published, total):
    kind = paper.get("source_kind")
    return {
        "batch_id": batch,
        "id": paper["id"],
        "title": str(paper.get("title") or paper["id"])[:180],
        "level": paper["level"] if paper.get("level") in ("J", "S") else None,
        "year": paper["year"] if type(paper.get("year")) is int else None,
        "source_kind": kind if kind in ("official_paper", "simulation", "original_demo") else None,
        "published_count": published,
        "candidate_count": total,
    }


@router.get("")
async def papers(response: Response, session: SessionDep, user: CurrentUser, offset: PageOffset = 0):
    response.headers["Cache-Control"] = "no-store"
    batch_id = WrittenQuestionVersion.batch_id
    paper_id = WrittenCandidate.paper_id
    rows = (await session.execute(
        visible_query().with_only_columns(batch_id, paper_id, func.count(WrittenQuestion.id))
        .group_by(batch_id, paper_id)
        .order_by(func.max(WrittenQuestionVersion.created_at).desc(), batch_id, paper_id)
        .offset(offset).limit(PAGE_SIZE + 1)
    )).all()
    packages = {}
    items = []
    for batch, paper, count in rows[:PAGE_SIZE]:
        if batch not in packages:
            packet = await load_payload(session, await session.get(WrittenImportBatch, batch))
            packages[batch] = (
                {record["id"]: record for record in packet["papers"]},
                Counter(row["paper_id"] for row in packet["questions"]),
            )
        cards, counts = packages[batch]
        items.append(paper_out(batch, cards[paper], count, counts[paper]))
    return {"items": items, "has_more": len(rows) > PAGE_SIZE}


@router.get("/{batch_id}/{paper_id}")
async def paper_questions(
    batch_id: UUID, paper_id: PaperId, response: Response, session: SessionDep,
    user: CurrentUser, offset: PageOffset = 0,
):
    response.headers["Cache-Control"] = "no-store"
    batch = str(batch_id)
    rows = (await session.execute(
        visible_query().add_columns(WrittenCandidate.external_id).where(
            WrittenQuestionVersion.batch_id == batch, WrittenCandidate.paper_id == paper_id,
        )
    )).all()
    if not rows:
        raise HTTPException(404, "这套试卷暂无上架题目")
    packet = await load_payload(session, await session.get(WrittenImportBatch, batch))
    paper = next(record for record in packet["papers"] if record["id"] == paper_id)
    original = [row for row in packet["questions"] if row["paper_id"] == paper_id]
    available = {external_id: (question, version) for question, version, external_id in rows}
    ordered = [
        {
            **summary(*available[row["id"]]),
            "number": str(row.get("original_number") or position)[:32],
        }
        for position, row in enumerate(original, 1) if row["id"] in available
    ]
    return {
        "paper": paper_out(batch, paper, len(rows), len(original)),
        "items": ordered[offset:offset + PAGE_SIZE],
        "has_more": len(ordered) > offset + PAGE_SIZE,
    }
