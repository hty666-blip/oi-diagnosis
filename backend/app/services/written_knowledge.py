"""仅按同一题包及知识点匹配当前审核版本。"""

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.domain.models import WrittenKnowledgeEntry, WrittenKnowledgeVersion


def published_query():
    return (
        select(WrittenKnowledgeEntry, WrittenKnowledgeVersion)
        .join(
            WrittenKnowledgeVersion,
            and_(
                WrittenKnowledgeVersion.entry_id == WrittenKnowledgeEntry.id,
                WrittenKnowledgeVersion.revision == WrittenKnowledgeEntry.published_revision,
            ),
        )
        .where(WrittenKnowledgeEntry.is_active.is_(True))
        .options(defer(WrittenKnowledgeEntry.body))
    )


def public_article(entry: WrittenKnowledgeEntry, version: WrittenKnowledgeVersion) -> dict:
    return {
        "id": entry.id,
        "point_id": entry.point_id,
        "point_name": entry.point_name,
        "revision": version.revision,
        "title": version.title,
        "body": version.body,
        "source": version.source,
        "published_at": version.created_at,
    }


async def help_knowledge(session: AsyncSession, batch_id: str, snapshot: dict):
    primary = snapshot.get("primary_knowledge_point_id")
    points = [p for p in [primary, *snapshot.get("secondary_knowledge_point_ids", [])] if p]
    if not points:
        return None, False
    rows = (
        await session.execute(
            select(WrittenKnowledgeEntry.point_id, WrittenKnowledgeVersion)
            .outerjoin(
                WrittenKnowledgeVersion,
                and_(
                    WrittenKnowledgeVersion.entry_id == WrittenKnowledgeEntry.id,
                    WrittenKnowledgeVersion.revision == WrittenKnowledgeEntry.published_revision,
                    WrittenKnowledgeEntry.is_active.is_(True),
                ),
            )
            .where(
                WrittenKnowledgeEntry.batch_id == batch_id,
                WrittenKnowledgeEntry.point_id.in_(points),
            )
        )
    ).all()
    by_point = dict(rows)
    for point in points:
        if version := by_point.get(point):
            return {
                "id": point,
                "entry_id": version.entry_id,
                "revision": version.revision,
                "title": version.title,
                "body": version.body[:1500],
                "source": version.source,
                "excerpt": len(version.body) > 1500,
            }, True
    return None, primary in by_point
