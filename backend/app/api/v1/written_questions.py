"""学生只读已发布题面；答案、解析、审核备注和采集记录不会返回。"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.api.input_types import ObjectPathId
from app.domain.models import (
    WrittenCandidate,
    WrittenImportAsset,
    WrittenQuestion,
    WrittenQuestionVersion,
)
from app.services.written_bank_storage import load_bytes, load_snapshot

router = APIRouter(prefix="/written/questions", tags=["written-questions"])


def visible_query():
    return (
        select(WrittenQuestion, WrittenQuestionVersion)
        .join(
            WrittenQuestionVersion,
            (WrittenQuestionVersion.question_id == WrittenQuestion.id)
            & (WrittenQuestionVersion.revision == WrittenQuestion.revision),
        )
        .join(WrittenCandidate, WrittenCandidate.id == WrittenQuestion.candidate_id)
        .where(
            WrittenQuestion.is_active.is_(True),
            WrittenCandidate.review_status == "approved",
            WrittenQuestionVersion.review_revision == WrittenCandidate.revision,
        )
    )


def summary(question, version):
    data = question.public_metadata
    return {
        "id": question.id,
        "revision": question.revision,
        "version_id": version.id,
        **{key: data.get(key) for key in ["stem", "question_type", "level", "score"]},
    }


async def get_visible(session, question_id):
    row = (await session.execute(visible_query().where(WrittenQuestion.id == question_id))).first()
    if row is None:
        raise HTTPException(404, "题目尚未发布或已下架")
    return row


@router.get("")
async def catalog(session: SessionDep, user: CurrentUser, offset: int = Query(0, ge=0, le=100000)):
    rows = (
        await session.execute(visible_query().order_by(WrittenQuestion.id).offset(offset).limit(26))
    ).all()
    return {"items": [summary(q, v) for q, v in rows[:25]], "has_more": len(rows) > 25}


@router.get("/{question_id}")
async def read_question(question_id: ObjectPathId, session: SessionDep, user: CurrentUser):
    question, version = await get_visible(session, question_id)
    data = await load_snapshot(session, version.batch_id, version.snapshot_metadata)
    return {
        "id": question.id,
        "revision": version.revision,
        "version_id": version.id,
        **{
            key: data.get(key)
            for key in [
                "stem",
                "shared_stem",
                "question_type",
                "level",
                "score",
                "primary_knowledge_point_id",
                "secondary_knowledge_point_ids",
            ]
        },
        "options": data["options"]
        if data["question_type"] in {"single_choice", "multiple_choice"}
        else [],
        "assets": [
            f"/api/v1/written/questions/{question.id}/assets/{i}?version_id={version.id}"
            for i in range(len(data.get("asset_paths", [])))
        ],
    }


@router.get("/{question_id}/assets/{index}")
async def image(
    question_id: ObjectPathId, index: int, version_id: UUID, session: SessionDep, user: CurrentUser
):
    _, version = await get_visible(session, question_id)
    if str(version_id) != version.id:
        raise HTTPException(409, "题目版本已变化，请重新打开题目")
    data = await load_snapshot(session, version.batch_id, version.snapshot_metadata)
    paths = data.get("asset_paths", [])
    if not 0 <= index < len(paths):
        raise HTTPException(404, "图片不存在")
    asset = await session.scalar(
        select(WrittenImportAsset).where(
            WrittenImportAsset.batch_id == version.batch_id, WrittenImportAsset.path == paths[index]
        )
    )
    if not asset:
        raise HTTPException(409, "图片快照缺失，请联系管理员")
    content = await load_bytes(session, version.batch_id, asset.path, asset.digest)
    return Response(
        content,
        media_type=asset.media_type,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
