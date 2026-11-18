"""控制错题复练的归属、题目版本及已保存的进度。"""

from collections import defaultdict
from math import fsum

from fastapi import HTTPException
from sqlalchemy import case, func, select

from app.domain.models import (
    User,
    WrittenAttempt,
    WrittenCandidate,
    WrittenExam,
    WrittenExamAttempt,
    WrittenExamItem,
    WrittenKnowledgeEntry,
    WrittenKnowledgeVersion,
    WrittenQuestion,
    WrittenQuestionVersion,
)
from app.services.written_assignments import active_students
from app.services.written_bank_storage import load_snapshot
from app.services.written_knowledge import published_query
from app.services.written_learning import (
    attempted_exercises,
    catalog_level,
    exercise,
    frozen_topic,
    recommendations,
)
from app.services.written_review import visible_query

CLASS_RESULT_LIMIT = 25


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


def latest_exam_attempt_ids(exam_ids: list[int]):
    return (
        select(func.max(WrittenAttempt.id))
        .join(WrittenExamAttempt, WrittenExamAttempt.attempt_id == WrittenAttempt.id)
        .join(WrittenExam, WrittenExam.id == WrittenExamAttempt.exam_id)
        .where(WrittenExam.id.in_(exam_ids), WrittenAttempt.student_id == WrittenExam.student_id)
        .group_by(WrittenExam.id, WrittenAttempt.question_id, WrittenAttempt.version_id)
    )


async def class_exam_results(session, class_id: int, before_id: int | None):
    students = active_students(class_id).with_only_columns(User.id)
    query = (
        select(WrittenExam.id, WrittenExam.title, WrittenExam.submitted_at, User.display_name, User.username)
        .join(User, User.id == WrittenExam.student_id)
        .where(WrittenExam.student_id.in_(students), WrittenExam.submitted_at.is_not(None))
    )
    if before_id is not None:
        query = query.where(WrittenExam.id < before_id)
    rows = (await session.execute(query.order_by(WrittenExam.id.desc()).limit(CLASS_RESULT_LIMIT + 1))).all()
    page = rows[:CLASS_RESULT_LIMIT]
    if not page:
        return {"items": [], "next_before_id": None}

    exam_ids = [row.id for row in page]
    cards = defaultdict(list)
    for item in await session.execute(select(
        WrittenExamItem.exam_id, WrittenExamItem.question_id, WrittenExamItem.version_id,
        WrittenExamItem.earned_score, WrittenExamItem.max_score,
    ).where(WrittenExamItem.exam_id.in_(exam_ids))):
        cards[item.exam_id].append(item)
    last_correct = await session.execute(
        select(WrittenExamAttempt.exam_id, WrittenAttempt.question_id, WrittenAttempt.version_id)
        .join(WrittenAttempt, WrittenAttempt.id == WrittenExamAttempt.attempt_id)
        .where(WrittenAttempt.id.in_(latest_exam_attempt_ids(exam_ids)), WrittenAttempt.correct.is_(True))
    )
    corrected = {(exam_id, question_id, version_id) for exam_id, question_id, version_id in last_correct}

    items = []
    for row in page:
        card = cards[row.id]
        missed_items = [item for item in card if item.earned_score < item.max_score]
        items.append({
            "id": row.id, "student_name": row.display_name or row.username, "title": row.title,
            "submitted_at": row.submitted_at,
            "earned_score": fsum(item.earned_score for item in card),
            "max_score": fsum(item.max_score for item in card),
            "missed_count": len(missed_items),
            "corrected_count": sum((row.id, item.question_id, item.version_id) in corrected for item in missed_items),
        })
    return {"items": items, "next_before_id": page[-1].id if len(rows) > CLASS_RESULT_LIMIT else None}


async def review_items(session, student_id: int, exam_id: int) -> list[dict]:
    await reviewed_exam(session, student_id, exam_id)
    items = (await session.scalars(missed(exam_id).order_by(WrittenExamItem.position))).all()
    if not items:
        return []
    visible = (await session.execute(visible_query().where(
        WrittenQuestion.id.in_([item.question_id for item in items]),
    ))).all()
    current = {question.id: version.id for question, version in visible}
    history = {
        (question, version): {"id": ident, "correct": correct}
        for question, version, ident, correct in await session.execute(
            select(WrittenAttempt.question_id, WrittenAttempt.version_id, WrittenAttempt.id, WrittenAttempt.correct)
            .where(WrittenAttempt.id.in_(latest_exam_attempt_ids([exam_id])))
        )
    }
    return [{
        "question_id": item.question_id, "number": item.number, "version_id": item.version_id,
        "answered": item.response is not None,
        "available": current.get(item.question_id) == item.version_id,
        "latest_attempt": history.get((item.question_id, item.version_id)),
    } for item in items]


async def study_topics(session, student_id: int, exam_id: int) -> list[dict]:
    exam = await reviewed_exam(session, student_id, exam_id)
    batch = exam.batch_id
    version = WrittenQuestionVersion
    current_level = case((version.revision == WrittenQuestion.revision, catalog_level))
    rows = await session.execute(
        missed(exam_id).with_only_columns(WrittenExamItem.number, version.snapshot_metadata, current_level)
        .select_from(WrittenExamItem)
        .join(version, (version.id == WrittenExamItem.version_id) & (version.question_id == WrittenExamItem.question_id))
        .join(WrittenQuestion, WrittenQuestion.id == WrittenExamItem.question_id)
        .where(version.batch_id == batch).order_by(WrittenExamItem.position)
    )
    taxonomies = {}
    topics = {}
    for number, metadata, level in rows:
        snapshot = None
        if level is None or "learning_topic" not in metadata:
            snapshot = await load_snapshot(session, batch, metadata)
            level = snapshot["level"]
        point = await frozen_topic(session, batch, metadata, taxonomies, snapshot=snapshot)
        entry = topics.setdefault((batch, level, point["id"]), {
            "point": point, "level": level, "numbers": [],
        })
        entry["numbers"].append(number)
    if not topics:
        return []

    point_ids = {point_id for _, _, point_id in topics}
    entry = WrittenKnowledgeEntry
    articles = {
        point: {"id": ident, "title": title}
        for point, ident, title in await session.execute(published_query().with_only_columns(
            entry.point_id, entry.id, WrittenKnowledgeVersion.title,
        ).where(entry.batch_id == batch, entry.point_id.in_(point_ids)))
    }
    attempted = attempted_exercises(student_id).with_only_columns(version.batch_id, exercise).where(version.batch_id == batch)
    in_exam = (
        select(WrittenExam.batch_id, exercise).select_from(WrittenExam)
        .join(WrittenExamItem, WrittenExamItem.exam_id == WrittenExam.id)
        .join(WrittenQuestion, WrittenQuestion.id == WrittenExamItem.question_id)
        .join(WrittenCandidate, WrittenCandidate.id == WrittenQuestion.candidate_id)
        .where(WrittenExam.student_id == student_id, WrittenExam.batch_id == batch)
    )
    seen = {(batch_id, key) for batch_id, key in await session.execute(attempted.union(in_exam))}
    suggestions = await recommendations(session, set(topics), seen, taxonomies)
    return [{**item, "article": articles.get(key[2]), "next_question": suggestions.get(key)}
            for key, item in topics.items()]


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
