"""读取已发布题面，提交客观作答并查询本人的评分记录。"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import String, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, defer

from app.api.deps import CurrentUser, SessionDep, require_role
from app.api.input_types import MAX_OBJECT_ID, ObjectPathId
from app.core.text_validation import StorageText
from app.domain.models import (
    User,
    WrittenAssignmentAttempt,
    WrittenAttempt,
    WrittenCandidate,
    WrittenImportAsset,
    WrittenQuestion,
    WrittenQuestionVersion,
)
from app.services.admin_service import management_lock
from app.services.written_assignments import assignment_version
from app.services.written_bank_storage import load_bytes, load_snapshot
from app.services.written_grading import AttemptInput, is_correct
from app.services.written_review import visible_query

router = APIRouter(prefix="/written/questions", tags=["written-questions"])
attempts_router = APIRouter(prefix="/written/attempts", tags=["written-attempts"])
PracticeStudent = Annotated[User, Depends(require_role("student"))]
QuestionType = Literal["single_choice", "multiple_choice", "true_false", "fill_blank"]
PAGE_SIZE = 25


def summary(question, version):
    data = question.public_metadata
    return {
        "id": question.id,
        "revision": question.revision,
        "version_id": version.id,
        **{key: data.get(key) for key in ["stem", "question_type", "level", "score"]},
    }


async def find_visible(session: AsyncSession, question_id: int):
    return (await session.execute(visible_query().where(WrittenQuestion.id == question_id))).first()


async def get_visible(session: AsyncSession, question_id: int):
    row = await find_visible(session, question_id)
    if row is None:
        raise HTTPException(404, "题目尚未发布或已下架")
    return row


@router.get("")
async def catalog(
    session: SessionDep,
    user: CurrentUser,
    offset: int = Query(0, ge=0, le=100000),
    q: Annotated[StorageText, Query(max_length=120)] = "",
    level: Literal["J", "S"] | None = None,
    question_type: QuestionType | None = None,
):
    stmt = visible_query()
    keyword = q.strip()
    if keyword:
        stmt = stmt.where(
            or_(
                WrittenCandidate.search_text.icontains(keyword, autoescape=True),
                cast(WrittenQuestion.id, String).icontains(keyword, autoescape=True),
            )
        )
    if level:
        stmt = stmt.where(WrittenCandidate.level == level)
    if question_type:
        stmt = stmt.where(WrittenCandidate.question_type == question_type)
    rows = (
        await session.execute(stmt.order_by(WrittenQuestion.id).offset(offset).limit(PAGE_SIZE + 1))
    ).all()
    return {
        "items": [summary(q, v) for q, v in rows[:PAGE_SIZE]],
        "has_more": len(rows) > PAGE_SIZE,
    }


@router.get("/{question_id}")
async def read_question(question_id: ObjectPathId, session: SessionDep, user: CurrentUser):
    question, version = await get_visible(session, question_id)
    data = await load_snapshot(session, version.batch_id, version.snapshot_metadata)
    asset_paths = data.get("asset_paths", [])
    asset_urls = [
        f"/api/v1/written/questions/{question.id}/assets/{i}?version_id={version.id}"
        for i in range(len(asset_paths))
    ]
    return question_out(version, data, asset_urls)


def question_out(version: WrittenQuestionVersion, data: dict, asset_urls: list[str]) -> dict:
    return {
        "id": version.question_id,
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
        "options": [{"id": item["id"], "text": item["text"]} for item in data["options"]]
        if data["question_type"] in {"single_choice", "multiple_choice"}
        else [],
        "assets": asset_urls,
        "assets_by_path": dict(zip(data.get("asset_paths", []), asset_urls, strict=True)),
    }


@router.get("/{question_id}/assets/{index}")
async def image(
    question_id: ObjectPathId, index: int, version_id: UUID, session: SessionDep, user: CurrentUser
):
    _, version = await get_visible(session, question_id)
    if str(version_id) != version.id:
        raise HTTPException(409, "题目版本已变化，请重新打开题目")
    return await read_image(session, version, index)


async def read_image(session: AsyncSession, version: WrittenQuestionVersion, index: int):
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


def attempt_summary(attempt: WrittenAttempt, revision: int) -> dict:
    return {
        "id": attempt.id,
        "question_id": attempt.question_id,
        "version_id": attempt.version_id,
        "revision": revision,
        "correct": attempt.correct,
        "earned_score": attempt.earned_score,
        "max_score": attempt.max_score,
        "submitted_at": attempt.created_at,
    }


def attempt_out(
    attempt: WrittenAttempt, version: WrittenQuestionVersion, data: dict, assignment_id: int | None
) -> dict:
    urls = [
        f"/api/v1/written/attempts/{attempt.id}/assets/{i}"
        for i in range(len(data.get("asset_paths", [])))
    ]
    return {
        **attempt_summary(attempt, version.revision),
        "assignment_id": assignment_id,
        "response": attempt.response,
        "answer": data["answer"],
        "explanation": data["explanation"],
        "question": question_out(version, data, urls),
    }


def attempt_query():
    return select(WrittenAttempt, WrittenQuestionVersion).join(
        WrittenQuestionVersion,
        (WrittenQuestionVersion.id == WrittenAttempt.version_id)
        & (WrittenQuestionVersion.question_id == WrittenAttempt.question_id),
    )


async def owned_attempt(session: AsyncSession, student_id: int, attempt_id: int):
    row = (
        await session.execute(
            attempt_query().where(
                WrittenAttempt.student_id == student_id, WrittenAttempt.id == attempt_id
            )
        )
    ).first()
    if row is None:
        raise HTTPException(404, "作答记录不存在")
    return row


async def receipt(session: AsyncSession, student_id: int, request_id: str):
    return (
        await session.execute(
            attempt_query().where(
                WrittenAttempt.student_id == student_id, WrittenAttempt.request_id == request_id
            )
        )
    ).first()


def submission_conflict(code: str, message: str) -> HTTPException:
    return HTTPException(409, {"code": code, "message": message})


async def replay_attempt(
    session, attempt: WrittenAttempt, version: WrittenQuestionVersion, digest: str,
    assignment_id: int | None,
):
    if attempt.request_digest != digest:
        raise submission_conflict("written_request_conflict", "该请求编号已用于另一份作答")
    data = await load_snapshot(session, version.batch_id, version.snapshot_metadata)
    return {**attempt_out(attempt, version, data, assignment_id), "replayed": True}


@attempts_router.post("")
async def submit_attempt(
    body: AttemptInput,
    request: Request,
    response: Response,
    session: SessionDep,
    user: PracticeStudent,
):
    response.headers["Cache-Control"] = "no-store"
    request_id, version_id, digest = str(body.request_id), str(body.version_id), body.digest()
    previous = await receipt(session, user.id, request_id)
    if previous is not None:
        return await replay_attempt(session, *previous, digest, body.assignment_id)

    # 先读取不可变快照，减少持有管理锁的时间。锁内重新确认上架状态与版本。
    version = await submission_version(session, user, body)
    snapshot = None
    if version is not None and version.id == version_id:
        snapshot = await load_snapshot(session, version.batch_id, version.snapshot_metadata)

    await management_lock(session)
    await session.refresh(user)
    if (
        not user.enabled
        or user.role != "student"
        or user.must_change_password
        or user.token_version != request.state.token_version
    ):
        raise HTTPException(401, "账号状态已变化，请重新登录")
    previous = await receipt(session, user.id, request_id)
    if previous is not None:
        await session.commit()
        return await replay_attempt(session, *previous, digest, body.assignment_id)

    version = await submission_version(session, user, body)
    if version is None:
        raise submission_conflict("written_question_unavailable", "该题尚未发布或已下架")
    if version.id != version_id:
        raise submission_conflict("written_version_changed", "题目版本已更新，请重新打开题目")
    if snapshot is None:
        snapshot = await load_snapshot(session, version.batch_id, version.snapshot_metadata)
    submitted = body.response.model_dump(mode="json")
    correct = is_correct(snapshot, submitted)
    score = snapshot["score"]
    attempt = WrittenAttempt(
        student_id=user.id,
        question_id=body.question_id,
        version_id=version_id,
        request_id=request_id,
        request_digest=digest,
        response=submitted,
        question_summary={
            "stem": snapshot["stem"][:160],
            "question_type": snapshot["question_type"],
            "level": snapshot["level"],
        },
        correct=correct,
        earned_score=score if correct else 0.0,
        max_score=score,
    )
    session.add(attempt)
    if body.assignment_id is not None:
        await session.flush()
        session.add(WrittenAssignmentAttempt(attempt_id=attempt.id, assignment_id=body.assignment_id))
    await session.commit()
    return {**attempt_out(attempt, version, snapshot, body.assignment_id), "replayed": False}


async def submission_version(session: AsyncSession, user: User, body: AttemptInput):
    if body.assignment_id is not None:
        return await assignment_version(
            session, user, body.assignment_id, body.question_id, submitting=True
        )
    row = await find_visible(session, body.question_id)
    return row[1] if row else None


@attempts_router.get("")
async def list_attempts(
    response: Response,
    session: SessionDep,
    user: PracticeStudent,
    before_id: int | None = Query(None, gt=0, le=MAX_OBJECT_ID),
    view: Literal["history", "wrong", "corrected"] = "history",
):
    response.headers["Cache-Control"] = "no-store"
    available = (
        visible_query()
        .where(WrittenQuestion.id == WrittenAttempt.question_id)
        .correlate(WrittenAttempt)
        .exists()
    )
    stmt = (
        attempt_query()
        .add_columns(available)
        .options(defer(WrittenAttempt.response), defer(WrittenAttempt.request_digest))
        .where(WrittenAttempt.student_id == user.id)
    )
    if view != "history":
        other = aliased(WrittenAttempt)
        related = select(other.id).where(
            other.student_id == user.id, other.question_id == WrittenAttempt.question_id
        )
        stmt = stmt.where(~related.where(other.id > WrittenAttempt.id).exists())
        if view == "wrong":
            stmt = stmt.where(WrittenAttempt.correct.is_(False))
        else:
            stmt = stmt.where(
                WrittenAttempt.correct.is_(True), related.where(other.correct.is_(False)).exists()
            )
    if before_id is not None:
        stmt = stmt.where(WrittenAttempt.id < before_id)
    rows = (
        await session.execute(stmt.order_by(WrittenAttempt.id.desc()).limit(PAGE_SIZE + 1))
    ).all()
    items = [
        {
            **attempt_summary(attempt, version.revision),
            **{key: attempt.question_summary[key] for key in ("stem", "question_type", "level")},
            "is_available": is_available,
        }
        for attempt, version, is_available in rows[:PAGE_SIZE]
    ]
    has_more = len(rows) > PAGE_SIZE
    return {
        "items": items,
        "has_more": has_more,
        "next_before_id": items[-1]["id"] if has_more else None,
    }


@attempts_router.get("/{attempt_id}")
async def read_attempt(
    attempt_id: ObjectPathId, response: Response, session: SessionDep, user: PracticeStudent
):
    response.headers["Cache-Control"] = "no-store"
    attempt, version = await owned_attempt(session, user.id, attempt_id)
    data = await load_snapshot(session, version.batch_id, version.snapshot_metadata)
    link = await session.get(WrittenAssignmentAttempt, attempt_id)
    return attempt_out(attempt, version, data, link.assignment_id if link else None)


@attempts_router.get("/{attempt_id}/assets/{index}")
async def attempt_image(
    attempt_id: ObjectPathId, index: int, session: SessionDep, user: PracticeStudent
):
    _, version = await owned_attempt(session, user.id, attempt_id)
    return await read_image(session, version, index)
