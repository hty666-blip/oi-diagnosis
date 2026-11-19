"""以本人的固定版本作答生成初赛学习路线和下一道练习。"""

from fastapi import APIRouter, Response
from sqlalchemy import func

from app.api.deps import SessionDep
from app.api.v1.written_questions import PracticeStudent
from app.domain.models import WrittenAttempt
from app.domain.models import WrittenQuestionVersion as Version
from app.services.written_learning import (
    attempted_exercises,
    exercise,
    frozen_topic,
    recommendations,
)

router = APIRouter(prefix="/written/progress", tags=["written-progress"])
RECENT_LIMIT = 100

@router.get("")
async def progress(response: Response, session: SessionDep, user: PracticeStudent):
    response.headers["Cache-Control"] = "no-store"
    mine = attempted_exercises(user.id)
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
