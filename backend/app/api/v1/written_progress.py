"""以本人的固定版本作答生成初赛学习路线和下一道练习。"""

from fastapi import APIRouter, Response
from sqlalchemy import case, func, select, tuple_

from app.api.deps import SessionDep
from app.api.v1.written_questions import PracticeStudent
from app.domain.models import WrittenAttempt, WrittenCandidate, WrittenImportBatch
from app.domain.models import WrittenQuestion as Question
from app.domain.models import WrittenQuestionVersion as Version
from app.services.written_bank_storage import load_payload, load_snapshot
from app.services.written_review import learning_topic, visible_query

router = APIRouter(prefix="/written/progress", tags=["written-progress"])
RECENT_LIMIT = 100

# 保守地把题包声明的完全重复题视为已见过的题，数值变体单独记录。
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


async def frozen_topic(session, batch_id, metadata, taxonomies):
    topic = metadata.get("learning_topic")
    if topic is not None:
        return topic
    # 已发布的旧快照和旧备份也能计算，按各自当时保存的主知识点归类。
    snapshot = await load_snapshot(session, batch_id, metadata)
    if batch_id not in taxonomies:
        batch = await session.get(WrittenImportBatch, batch_id)
        payload = await load_payload(session, batch)
        taxonomies[batch_id] = {node["id"]: node for node in payload["knowledge_points"]["nodes"]}
    return learning_topic(snapshot["primary_knowledge_point_id"], taxonomies[batch_id])


async def recommendations(session, pending, first_attempts, taxonomies):
    if not pending:
        return {}
    index = tuple_(Version.batch_id, catalog_level, topic_id)
    fields = (Question.id, Version.id, Version.batch_id, catalog_level, exercise)
    catalog = visible_query().with_only_columns(*fields).order_by(Question.id)
    suggestions = {}
    rows = await session.execute(catalog.add_columns(topic_id).where(index.in_(list(pending))))
    for question_id, version_id, batch, level, key, point in rows:
        if (batch, key) not in first_attempts:
            suggestions.setdefault((batch, level, point), {"id": question_id, "version_id": version_id})

    remaining = pending - suggestions.keys()
    if remaining:
        # 旧快照先限定题包、级别和扫描量，避免解压整库。
        pairs = {(batch, level) for batch, level, _ in remaining}
        legacy = catalog.add_columns(Version.snapshot_metadata).where(
            topic_id.is_(None), tuple_(Version.batch_id, catalog_level).in_(list(pairs))
        )
        rows = await session.execute(legacy.limit(RECENT_LIMIT))
        for question_id, version_id, batch, level, key, metadata in rows:
            if (batch, key) in first_attempts:
                continue
            point = await frozen_topic(session, batch, metadata, taxonomies)
            target = (batch, level, point["id"])
            if target in remaining:
                suggestions[target] = {"id": question_id, "version_id": version_id}
                remaining.remove(target)
                if not remaining:
                    break
    return suggestions


@router.get("")
async def progress(response: Response, session: SessionDep, user: PracticeStudent):
    response.headers["Cache-Control"] = "no-store"
    mine = (
        select(WrittenAttempt.id, Version.batch_id, exercise)
        .join(Question, Question.id == WrittenAttempt.question_id)
        .join(Version, (Version.id == WrittenAttempt.version_id) & (Version.question_id == Question.id))
        .join(WrittenCandidate, WrittenCandidate.id == Question.candidate_id)
        .where(WrittenAttempt.student_id == user.id)
    )
    recent = (
        await session.execute(
            mine.add_columns(
                WrittenAttempt.correct, WrittenAttempt.question_summary["level"].as_string(),
                Version.snapshot_metadata,
            ).order_by(WrittenAttempt.id.desc()).limit(RECENT_LIMIT)
        )
    ).all()
    if not recent:
        return {"items": [], "attempt_limit": RECENT_LIMIT}

    first_attempts = {
        (batch, key): ident
        for ident, batch, key in await session.execute(
            mine.with_only_columns(func.min(WrittenAttempt.id), Version.batch_id, exercise)
            .where(Version.batch_id.in_({row.batch_id for row in recent}))
            .group_by(Version.batch_id, exercise)
        )
    }
    taxonomies = {}
    by_version = {}
    topics = {}
    for ident, batch, key, correct, level, metadata in reversed(recent):
        version_key = (batch, metadata["key"])
        if version_key not in by_version:
            by_version[version_key] = await frozen_topic(session, batch, metadata, taxonomies)
        point = by_version[version_key]
        target = (batch, level, point["id"])
        item = topics.setdefault(target, {
            "batch_id": batch, "level": level, "point": point,
            "status": "practiced", "last_error_id": None, "review_attempt_id": None,
        })
        item["latest_attempt_id"] = ident
        item["attempt_count"] = item.get("attempt_count", 0) + 1
        if not correct:
            item.update(status="needs_review", last_error_id=ident, review_attempt_id=None)
        elif item["status"] == "needs_review" and first_attempts[(batch, key)] == ident:
            item.update(status="reviewed", review_attempt_id=ident)

    pending = {key for key, item in topics.items() if item["status"] == "needs_review"}
    suggestions = await recommendations(session, pending, first_attempts, taxonomies)
    for key, item in topics.items():
        item["next_question"] = suggestions.get(key)
    items = sorted(
        topics.values(),
        key=lambda item: (item["status"] != "needs_review", -item["latest_attempt_id"]),
    )
    return {"items": items, "attempt_limit": RECENT_LIMIT}
