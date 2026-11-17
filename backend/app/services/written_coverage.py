"""按原始候选和在架审核版本分别统计同批次主知识点覆盖。"""

from collections import Counter

from sqlalchemy import func, select

from app.domain.models import (
    WrittenCandidate,
    WrittenKnowledgeEntry,
    WrittenQuestion,
    WrittenQuestionVersion,
)
from app.services.written_bank_storage import load_snapshot
from app.services.written_knowledge import published_query
from app.services.written_review import visible_query

source_point = WrittenCandidate.record["primary_knowledge_point_id"].as_string()
source_level = WrittenCandidate.record["level"].as_string()


async def batch_coverage(session, batch_id, nodes):
    leaves = {node["id"]: node["name"][:160] for node in nodes if node.get("is_leaf") is True}
    pending = {}
    unmapped_pending = 0
    rows = await session.execute(
        select(source_point, source_level, func.count(WrittenCandidate.id))
        .where(
            WrittenCandidate.batch_id == batch_id,
            WrittenCandidate.review_status == "needs_review",
        )
        .group_by(source_point, source_level)
    )
    for point, level, count in rows:
        if point in leaves:
            pending[(point, level)] = int(count)
        else:
            unmapped_pending += int(count)

    published = Counter()
    rows = await session.execute(
        visible_query().with_only_columns(
            WrittenQuestionVersion.snapshot_metadata, WrittenQuestion.public_metadata["level"].as_string()
        ).where(WrittenQuestionVersion.batch_id == batch_id)
    )
    for metadata, level in rows:
        if topic := metadata.get("learning_topic"):
            point = topic["id"]
        else:
            snapshot = await load_snapshot(session, batch_id, metadata)
            point = snapshot["primary_knowledge_point_id"]
        published[(point, level)] += 1

    articles = dict((await session.execute(
        published_query().with_only_columns(
            WrittenKnowledgeEntry.point_id, WrittenKnowledgeEntry.published_revision
        ).where(WrittenKnowledgeEntry.batch_id == batch_id)
    )).all())
    return {
        "items": [
            {
                "id": point,
                "name": name,
                "pending_j": pending.get((point, "J"), 0),
                "pending_s": pending.get((point, "S"), 0),
                "pending_unclassified": pending.get((point, None), 0),
                "published_j": published[(point, "J")],
                "published_s": published[(point, "S")],
                "article_revision": articles.get(point),
            }
            for point, name in leaves.items()
        ],
        "unmapped_pending": unmapped_pending,
    }
