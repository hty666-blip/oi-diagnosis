"""管理员编审初赛知识条目，登录用户只读取已发布版本。"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import defer

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.api.input_types import ObjectPathId
from app.api.v1.written_questions import summary
from app.core.text_validation import StorageText
from app.domain.models import WrittenImportBatch, WrittenKnowledgeEntry, WrittenKnowledgeVersion
from app.services.admin_service import admin_write, audit
from app.services.written_bank_storage import load_payload
from app.services.written_knowledge import public_article, published_query, related_practice

admin_router = APIRouter(prefix="/admin/written-knowledge", tags=["admin-written-knowledge"])
router = APIRouter(prefix="/written/knowledge", tags=["written-knowledge"])
PAGE_SIZE = 25
PointId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")]


class Article(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: StorageText = Field(min_length=1, max_length=120)
    body: StorageText = Field(default="", max_length=3800)
    source: StorageText = Field(default="", max_length=500)

    @field_validator("title", "body", "source")
    @classmethod
    def strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("title")
    @classmethod
    def require_title(cls, value: str) -> str:
        if not value:
            raise ValueError("请填写标题")
        return value


class CreateArticle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch_id: UUID
    point_id: PointId
    article: Article


class EditArticle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(strict=True, ge=1, le=2**31 - 1)
    action: Literal["save", "publish", "unpublish"]
    article: Article | None = None
    review_note: StorageText = Field(default="", max_length=400)


async def leaf_points(session, batch_id: UUID) -> dict[str, str]:
    batch = await session.get(WrittenImportBatch, str(batch_id))
    if batch is None or batch.status != "staged":
        raise HTTPException(404, "请选择已暂存的题包批次")
    payload = await load_payload(session, batch)
    return {
        node["id"]: node["name"][:160]
        for node in payload["knowledge_points"]["nodes"]
        if node.get("is_leaf") is True
    }


def entry_summary(entry: WrittenKnowledgeEntry):
    return {
        "id": entry.id,
        "batch_id": entry.batch_id,
        "point_id": entry.point_id,
        "point_name": entry.point_name,
        "title": entry.title,
        "edit_version": entry.edit_version,
        "published_revision": entry.published_revision,
        "is_active": entry.is_active,
    }


def public_summary(entry: WrittenKnowledgeEntry, version: WrittenKnowledgeVersion):
    return {
        "id": entry.id,
        "point_id": entry.point_id,
        "point_name": entry.point_name,
        "title": version.title,
        "revision": version.revision,
        "published_at": version.created_at,
    }


async def entry_detail(session, entry: WrittenKnowledgeEntry):
    editions = (
        await session.execute(
            select(
                WrittenKnowledgeVersion.revision,
                WrittenKnowledgeVersion.title,
                WrittenKnowledgeVersion.created_at,
            )
            .where(WrittenKnowledgeVersion.entry_id == entry.id)
            .order_by(WrittenKnowledgeVersion.revision.desc())
            .limit(25)
        )
    ).all()
    return {
        **entry_summary(entry),
        "article": {"title": entry.title, "body": entry.body, "source": entry.source},
        "editions": [
            {"revision": revision, "title": title, "published_at": at}
            for revision, title, at in editions
        ],
    }


async def get_entry(session, entry_id: int):
    entry = await session.get(WrittenKnowledgeEntry, entry_id, populate_existing=True)
    if entry is None:
        raise HTTPException(404, "知识条目不存在")
    return entry


@admin_router.get("/topics")
async def topics(batch_id: UUID, session: SessionDep, user: AdminUser):
    values = await leaf_points(session, batch_id)
    return {"items": [{"id": point, "name": name} for point, name in values.items()]}


@admin_router.get("")
async def admin_list(
    session: SessionDep,
    user: AdminUser,
    q: Annotated[StorageText, Query(max_length=120)] = "",
    offset: int = Query(0, ge=0, le=100000),
):
    query = select(WrittenKnowledgeEntry).options(defer(WrittenKnowledgeEntry.body))
    if keyword := q.strip():
        query = query.where(
            or_(
                WrittenKnowledgeEntry.title.icontains(keyword, autoescape=True),
                WrittenKnowledgeEntry.point_name.icontains(keyword, autoescape=True),
                WrittenKnowledgeEntry.point_id.icontains(keyword, autoescape=True),
            )
        )
    rows = (
        await session.scalars(
            query.order_by(WrittenKnowledgeEntry.id.desc()).offset(offset).limit(PAGE_SIZE + 1)
        )
    ).all()
    return {
        "items": [entry_summary(entry) for entry in rows[:PAGE_SIZE]],
        "has_more": len(rows) > PAGE_SIZE,
    }


@admin_router.post("", status_code=201)
async def create(
    body: CreateArticle,
    request: Request,
    session: SessionDep,
    user: AdminUser,
):
    available = await leaf_points(session, body.batch_id)
    if body.point_id not in available:
        raise HTTPException(422, "知识点须来自该批次的叶子目录")
    await admin_write(session, user, request)
    batch_id = str(body.batch_id)
    existing = await session.scalar(
        select(WrittenKnowledgeEntry.id).where(
            WrittenKnowledgeEntry.batch_id == batch_id,
            WrittenKnowledgeEntry.point_id == body.point_id,
        )
    )
    if existing is not None:
        raise HTTPException(409, "该批次下的知识点已有条目，请在列表中编辑")
    entry = WrittenKnowledgeEntry(
        batch_id=batch_id,
        point_id=body.point_id,
        point_name=available[body.point_id],
        editor_id=user.id,
        **body.article.model_dump(),
    )
    session.add(entry)
    await session.flush()
    audit(
        session, user, "written.knowledge.create", str(entry.id), {}, {"point_id": entry.point_id}
    )
    await session.commit()
    return await entry_detail(session, entry)


@admin_router.get("/{entry_id}")
async def admin_detail(entry_id: ObjectPathId, session: SessionDep, user: AdminUser):
    return await entry_detail(session, await get_entry(session, entry_id))


@admin_router.get("/{entry_id}/versions/{revision}")
async def past_version(
    entry_id: ObjectPathId, revision: ObjectPathId, session: SessionDep, user: AdminUser
):
    entry = await get_entry(session, entry_id)
    version = await session.scalar(
        select(WrittenKnowledgeVersion).where(
            WrittenKnowledgeVersion.entry_id == entry.id,
            WrittenKnowledgeVersion.revision == revision,
        )
    )
    if version is None:
        raise HTTPException(404, "发布版本不存在")
    return {**public_article(entry, version), "review_note": version.review_note}


@admin_router.post("/{entry_id}")
async def write_article(
    entry_id: ObjectPathId,
    body: EditArticle,
    request: Request,
    session: SessionDep,
    user: AdminUser,
):
    await admin_write(session, user, request)
    entry = await get_entry(session, entry_id)
    if body.expected_version != entry.edit_version:
        raise HTTPException(409, "条目已有更新，请保留草稿并重新打开条目")
    before = entry_summary(entry)
    if body.action == "unpublish":
        if not entry.is_active:
            raise HTTPException(409, "当前没有已上架版本")
        entry.is_active = False
    else:
        if body.article is None:
            raise HTTPException(422, "请填写条目内容")
        article = body.article
        if body.action == "publish":
            if (
                len(article.body) < 20
                or len(article.source) < 4
                or len(body.review_note.strip()) < 4
            ):
                raise HTTPException(422, "发布前请补齐知识正文、资料来源和审核说明")
            entry.published_revision += 1
            session.add(
                WrittenKnowledgeVersion(
                    entry_id=entry.id,
                    revision=entry.published_revision,
                    review_note=body.review_note.strip(),
                    reviewer_id=user.id,
                    **article.model_dump(),
                )
            )
            entry.is_active = True
        entry.title, entry.body, entry.source = article.title, article.body, article.source
    entry.edit_version += 1
    entry.editor_id = user.id
    audit(
        session,
        user,
        "written.knowledge." + body.action,
        str(entry.id),
        before,
        entry_summary(entry),
    )
    await session.commit()
    return await entry_detail(session, entry)


@router.get("")
async def catalog(
    session: SessionDep,
    user: CurrentUser,
    response: Response,
    q: Annotated[StorageText, Query(max_length=120)] = "",
    offset: int = Query(0, ge=0, le=100000),
):
    query = published_query().options(defer(WrittenKnowledgeVersion.body))
    if keyword := q.strip():
        query = query.where(
            or_(
                WrittenKnowledgeVersion.title.icontains(keyword, autoescape=True),
                WrittenKnowledgeEntry.point_name.icontains(keyword, autoescape=True),
                WrittenKnowledgeEntry.point_id.icontains(keyword, autoescape=True),
            )
        )
    rows = (
        await session.execute(
            query.order_by(WrittenKnowledgeEntry.id).offset(offset).limit(PAGE_SIZE + 1)
        )
    ).all()
    response.headers["Cache-Control"] = "no-store"
    return {
        "items": [public_summary(e, v) for e, v in rows[:PAGE_SIZE]],
        "has_more": len(rows) > PAGE_SIZE,
    }


@router.get("/{entry_id}")
async def read(entry_id: ObjectPathId, session: SessionDep, user: CurrentUser, response: Response):
    row = (
        await session.execute(published_query().where(WrittenKnowledgeEntry.id == entry_id))
    ).first()
    if row is None:
        raise HTTPException(404, "条目尚未发布或已下架")
    response.headers["Cache-Control"] = "no-store"
    entry, version = row
    questions = await related_practice(session, entry)
    return {
        **public_article(entry, version),
        "related_questions": [summary(question, revision) for question, revision in questions],
    }
