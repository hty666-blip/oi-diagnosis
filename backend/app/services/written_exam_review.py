"""控制错题复练的归属、题目版本及已保存的进度。"""

from fastapi import HTTPException
from sqlalchemy import func, select

from app.domain.models import (
    WrittenAttempt,
    WrittenExam,
    WrittenExamAttempt,
    WrittenExamItem,
    WrittenQuestion,
)
from app.services.written_review import visible_query


async def reviewed_exam(session, student_id: int, exam_id: int):
    exam = await session.scalar(select(WrittenExam).where(
        WrittenExam.id == exam_id, WrittenExam.student_id == student_id,
    ))
    if exam is None:
        raise HTTPException(404, "模拟答卷不存在")
    if exam.submitted_at is None:
        raise HTTPException(409, "请先交卷再复练错题")
    return exam


def missed(exam_id: int):
    return select(WrittenExamItem).where(
        WrittenExamItem.exam_id == exam_id, WrittenExamItem.earned_score < WrittenExamItem.max_score,
    )


async def review_items(session, student_id: int, exam_id: int) -> list[dict]:
    await reviewed_exam(session, student_id, exam_id)
    items = (await session.scalars(missed(exam_id).order_by(WrittenExamItem.position))).all()
    if not items:
        return []
    visible = (await session.execute(visible_query().where(
        WrittenQuestion.id.in_([item.question_id for item in items]),
    ))).all()
    current = {question.id: version.id for question, version in visible}
    latest = (
        select(func.max(WrittenAttempt.id))
        .join(WrittenExamAttempt, WrittenExamAttempt.attempt_id == WrittenAttempt.id)
        .where(WrittenExamAttempt.exam_id == exam_id, WrittenAttempt.student_id == student_id)
        .group_by(WrittenAttempt.question_id, WrittenAttempt.version_id)
    )
    history = {
        (question, version): {"id": ident, "correct": correct}
        for question, version, ident, correct in await session.execute(
            select(WrittenAttempt.question_id, WrittenAttempt.version_id, WrittenAttempt.id, WrittenAttempt.correct)
            .where(WrittenAttempt.id.in_(latest))
        )
    }
    return [{
        "question_id": item.question_id, "number": item.number, "version_id": item.version_id,
        "answered": item.response is not None,
        "available": current.get(item.question_id) == item.version_id,
        "latest_attempt": history.get((item.question_id, item.version_id)),
    } for item in items]


async def exam_retry_version(session, student_id: int, exam_id: int, question_id: int):
    await reviewed_exam(session, student_id, exam_id)
    item = await session.scalar(missed(exam_id).where(WrittenExamItem.question_id == question_id))
    if item is None:
        raise HTTPException(404, "这题不在本答卷的错题复练清单中")
    row = (await session.execute(visible_query().where(WrittenQuestion.id == question_id))).first()
    if row is None or row[1].id != item.version_id:
        raise HTTPException(409, {
            "code": "written_exam_retry_unavailable",
            "message": "原题版本已下架或更新，不能按这份答卷复练；仍可回看原题和卷分",
        })
    return row[1]
