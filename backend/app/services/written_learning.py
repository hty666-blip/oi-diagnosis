"""从保存的题目版本识别知识点，并选择本级别尚未见过的客观题。"""

from sqlalchemy import case, func, select, tuple_

from app.domain.models import WrittenAttempt, WrittenCandidate, WrittenImportBatch
from app.domain.models import WrittenQuestion as Question
from app.domain.models import WrittenQuestionVersion as Version
from app.services.written_bank_storage import load_payload, load_snapshot
from app.services.written_review import learning_topic, visible_query

# 题包声明的完全重复题视为同一道练习。数值变体单独记录。
exercise = case(
    (
        WrittenCandidate.normalized["source_relation"].as_string() == "exact_duplicate",
        func.coalesce(
            WrittenCandidate.normalized["related_question_id"].as_string(), WrittenCandidate.external_id
        ),
    ),
    else_=WrittenCandidate.external_id,
)
topic_id = Version.snapshot_metadata["learning_topic"]["id"].as_string()
catalog_level = Question.public_metadata["level"].as_string()


def attempted_exercises(student_id: int):
    return (
        select(WrittenAttempt.id, Version.batch_id, exercise)
        .join(Question, Question.id == WrittenAttempt.question_id)
        .join(Version, (Version.id == WrittenAttempt.version_id) & (Version.question_id == Question.id))
        .join(WrittenCandidate, WrittenCandidate.id == Question.candidate_id)
        .where(WrittenAttempt.student_id == student_id,
               WrittenAttempt.question_summary["question_type"].as_string() != "short_answer")
    )


async def frozen_topic(session, batch_id, metadata, taxonomies, *, snapshot=None):
    topic = metadata.get("learning_topic")
    if topic is not None:
        return topic
    # 旧快照按当时保存的主知识点归类。
    if snapshot is None:
        snapshot = await load_snapshot(session, batch_id, metadata)
    if batch_id not in taxonomies:
        batch = await session.get(WrittenImportBatch, batch_id)
        payload = await load_payload(session, batch)
        taxonomies[batch_id] = {node["id"]: node for node in payload["knowledge_points"]["nodes"]}
    return learning_topic(snapshot["primary_knowledge_point_id"], taxonomies[batch_id])


async def recommendations(session, pending, seen, taxonomies):
    if not pending:
        return {}
    index = tuple_(Version.batch_id, catalog_level, topic_id)
    fields = (Question.id, Version.id, Version.batch_id, catalog_level, exercise)
    catalog = visible_query().with_only_columns(*fields).where(
        WrittenCandidate.question_type != "short_answer",
    ).order_by(Question.id)
    suggestions = {}
    rows = await session.execute(catalog.add_columns(topic_id).where(index.in_(list(pending))))
    for question_id, version_id, batch, level, key, point in rows:
        if (batch, key) not in seen:
            suggestions.setdefault((batch, level, point), {"id": question_id, "version_id": version_id})

    remaining = pending - suggestions.keys()
    if remaining:
        # 旧快照先限定题包、级别和扫描量，避免解压整库。
        pairs = {(batch, level) for batch, level, _ in remaining}
        legacy = catalog.add_columns(Version.snapshot_metadata).where(
            topic_id.is_(None), tuple_(Version.batch_id, catalog_level).in_(list(pairs))
        )
        rows = await session.execute(legacy.limit(100))
        for question_id, version_id, batch, level, key, metadata in rows:
            if (batch, key) in seen:
                continue
            point = await frozen_topic(session, batch, metadata, taxonomies)
            target = (batch, level, point["id"])
            if target in remaining:
                suggestions[target] = {"id": question_id, "version_id": version_id}
                remaining.remove(target)
                if not remaining:
                    break
    return suggestions
