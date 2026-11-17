"""为答疑提取同一学生、题包和主知识点的早期作答事实。"""

from fastapi import HTTPException
from sqlalchemy import or_, select

from app.domain.models import WrittenAttempt, WrittenQuestionVersion
from app.services.written_bank_storage import snapshot_primary_point


async def earlier_practice(session, attempt, batch_id, snapshot):
    primary = snapshot.get("primary_knowledge_point_id")
    if not primary:
        return []
    topic = WrittenQuestionVersion.snapshot_metadata["learning_topic"]["id"].as_string()
    stmt = (
        select(
            WrittenAttempt.id, WrittenAttempt.question_id, WrittenQuestionVersion.revision,
            WrittenAttempt.correct, WrittenAttempt.question_summary["question_type"].as_string(),
            WrittenQuestionVersion.snapshot_metadata,
        )
        .join(WrittenQuestionVersion, (WrittenQuestionVersion.id == WrittenAttempt.version_id)
              & (WrittenQuestionVersion.question_id == WrittenAttempt.question_id))
        .where(
            WrittenAttempt.student_id == attempt.student_id,
            WrittenAttempt.id < attempt.id,
            WrittenAttempt.question_id != attempt.question_id,
            WrittenQuestionVersion.batch_id == batch_id,
            WrittenAttempt.question_summary["level"].as_string() == snapshot["level"],
            or_(topic == primary, topic.is_(None)),
        )
        .order_by(WrittenAttempt.id.desc())
        .limit(24)
    )
    found = {}
    for ident, question_id, revision, correct, question_type, metadata in await session.execute(stmt):
        if question_id in found:
            continue
        try:
            point = await snapshot_primary_point(session, batch_id, metadata)
        except HTTPException as failure:
            if failure.status_code != 409:
                raise
            continue
        if point != primary:
            continue
        found[question_id] = {
            "attempt_id": ident,
            "question_id": question_id,
            "revision": revision,
            "question_type": question_type,
            "correct": correct,
        }
        if len(found) == 3:
            break
    return list(found.values())
