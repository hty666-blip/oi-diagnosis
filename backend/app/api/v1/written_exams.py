"""服务端计时的整卷模拟。成绩和答卷归属当前学生。"""

from datetime import UTC, datetime, timedelta
from math import fsum
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.deps import SessionDep
from app.api.input_types import MAX_OBJECT_ID, ObjectPathId
from app.api.v1.written_papers import PaperId, paper_contents
from app.api.v1.written_questions import PracticeStudent, question_out, read_image
from app.domain.models import User, WrittenExam, WrittenExamItem, WrittenQuestionVersion
from app.services.admin_service import management_lock
from app.services.written_bank_storage import load_snapshot
from app.services.written_grading import BooleanResponse, OptionResponse, TextResponse, is_correct


def prevent_cache(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/written/exams", tags=["written-exams"], dependencies=[Depends(prevent_cache)])
Answer = Annotated[OptionResponse | BooleanResponse | TextResponse, Field(discriminator="kind")]


class DraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(strict=True, ge=0, le=2147483646)
    response: Answer | None


def now() -> datetime:
    return datetime.now(UTC)


def status(exam, instant):
    if exam.submitted_at is not None:
        return "submitted"
    return "expired" if instant >= exam.deadline else "working"


def exam_out(exam, instant):
    return {
        "id": exam.id, "title": exam.title, "started_at": exam.started_at, "deadline": exam.deadline,
        "submitted_at": exam.submitted_at, "server_time": instant, "status": status(exam, instant),
    }


def item_out(item):
    return {
        "question_id": item.question_id, "number": item.number, "revision": item.revision,
        "answered": item.response is not None, "max_score": item.max_score, "earned_score": item.earned_score,
    }


async def owned(session, student_id, exam_id, *, lock=False):
    query = select(WrittenExam).where(WrittenExam.id == exam_id, WrittenExam.student_id == student_id)
    exam = await session.scalar(query.with_for_update() if lock else query)
    if exam is None:
        raise HTTPException(404, "模拟答卷不存在")
    return exam


async def require_current_student(session, request, user):
    current = await session.scalar(
        select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True)
    )
    if (current is None or not current.enabled or current.role != "student"
            or current.must_change_password or current.token_version != request.state.token_version):
        raise HTTPException(401, "账号状态已变化，请重新登录")


async def items(session, exam):
    return (await session.scalars(
        select(WrittenExamItem).where(WrittenExamItem.exam_id == exam.id).order_by(WrittenExamItem.position)
    )).all()


async def detail(session, exam):
    frozen = await items(session, exam)
    submitted = exam.submitted_at is not None
    return {
        **exam_out(exam, now()),
        "items": [item_out(item) for item in frozen],
        "max_score": fsum(item.max_score for item in frozen),
        "earned_score": fsum(item.earned_score for item in frozen) if submitted else None,
    }


async def frozen_question(session, exam, question_id):
    item = await session.get(WrittenExamItem, (exam.id, question_id))
    if item is None:
        raise HTTPException(404, "题目不在这份答卷中")
    version = await session.get(WrittenQuestionVersion, item.version_id)
    if version is None or version.question_id != question_id or version.batch_id != exam.batch_id:
        raise HTTPException(409, "模拟题面版本不完整，请联系管理员")
    return item, version


@router.get("")
async def history(
    session: SessionDep, user: PracticeStudent,
    before_id: Annotated[int | None, Query(gt=0, le=MAX_OBJECT_ID)] = None,
):
    query = select(WrittenExam).where(WrittenExam.student_id == user.id)
    if before_id is not None:
        query = query.where(WrittenExam.id < before_id)
    rows = (await session.scalars(query.order_by(WrittenExam.id.desc()).limit(26))).all()
    instant = now()
    return {"items": [exam_out(exam, instant) for exam in rows[:25]], "next_before_id": rows[24].id if len(rows) > 25 else None}


@router.post("/start/{batch_id}/{paper_id}")
async def start(batch_id: UUID, paper_id: PaperId, request: Request, session: SessionDep, user: PracticeStudent):
    await management_lock(session)
    await require_current_student(session, request, user)
    batch = str(batch_id)
    exam = await session.scalar(select(WrittenExam).where(
        WrittenExam.student_id == user.id, WrittenExam.batch_id == batch, WrittenExam.paper_id == paper_id,
    ))
    if exam is None:
        paper, ordered = await paper_contents(session, batch, paper_id)
        if not paper["mock_available"]:
            raise HTTPException(409, "试卷须完整上架，且有一致的分值与标注时长")
        instant = now()
        exam = WrittenExam(student_id=user.id, batch_id=batch, paper_id=paper_id, title=paper["title"],
                           started_at=instant, deadline=instant + timedelta(minutes=paper["duration_minutes"]))
        session.add(exam)
        await session.flush()
        session.add_all([
            WrittenExamItem(exam_id=exam.id, question_id=item["id"], version_id=item["version_id"],
                            position=position, number=item["number"], max_score=item["score"])
            for position, item in enumerate(ordered)
        ])
    await session.commit()
    return await detail(session, exam)


@router.get("/{exam_id}")
async def read_exam(exam_id: ObjectPathId, session: SessionDep, user: PracticeStudent):
    return await detail(session, await owned(session, user.id, exam_id))


@router.get("/{exam_id}/questions/{question_id}")
async def read_question(exam_id: ObjectPathId, question_id: ObjectPathId, session: SessionDep, user: PracticeStudent):
    exam = await owned(session, user.id, exam_id)
    item, version = await frozen_question(session, exam, question_id)
    snapshot = await load_snapshot(session, version.batch_id, version.snapshot_metadata)
    urls = [f"/api/v1/written/exams/{exam.id}/questions/{question_id}/assets/{index}"
            for index in range(len(snapshot.get("asset_paths", [])))]
    result = {"question": question_out(version, snapshot, urls), "card": item_out(item), "response": item.response}
    if exam.submitted_at is not None:
        result.update(answer=snapshot["answer"], explanation=snapshot["explanation"])
    return result


@router.get("/{exam_id}/questions/{question_id}/assets/{index}")
async def asset(exam_id: ObjectPathId, question_id: ObjectPathId, index: int, session: SessionDep, user: PracticeStudent):
    exam = await owned(session, user.id, exam_id)
    _, version = await frozen_question(session, exam, question_id)
    return await read_image(session, version, index)


@router.put("/{exam_id}/questions/{question_id}")
async def save(exam_id: ObjectPathId, question_id: ObjectPathId, body: DraftInput, request: Request,
               session: SessionDep, user: PracticeStudent):
    await require_current_student(session, request, user)
    exam = await owned(session, user.id, exam_id, lock=True)
    if status(exam, now()) != "working":
        raise HTTPException(409, {"code": "written_exam_closed", "message": "模拟已结束，答题卡已冻结"})
    item, version = await frozen_question(session, exam, question_id)
    answer = body.response.model_dump(mode="json") if body.response is not None else None
    if answer == item.response:
        await session.commit()
        return item_out(item)
    if body.expected_revision != item.revision:
        raise HTTPException(409, {"code": "written_exam_draft_changed", "message": "答题卡已在另一页面更新，请重新读取"})
    if answer is not None:
        snapshot = await load_snapshot(session, version.batch_id, version.snapshot_metadata)
        is_correct(snapshot, answer)
    item.response = answer
    item.revision += 1
    await session.commit()
    return item_out(item)


@router.post("/{exam_id}/submit")
async def submit(exam_id: ObjectPathId, request: Request, session: SessionDep, user: PracticeStudent):
    await require_current_student(session, request, user)
    exam = await owned(session, user.id, exam_id, lock=True)
    if exam.submitted_at is None:
        instant = now()
        for item in await items(session, exam):
            item.earned_score = 0.0
            if item.response is not None:
                _, version = await frozen_question(session, exam, item.question_id)
                snapshot = await load_snapshot(session, version.batch_id, version.snapshot_metadata)
                if is_correct(snapshot, item.response):
                    item.earned_score = item.max_score
        exam.submitted_at = instant
    await session.commit()
    return await detail(session, exam)
