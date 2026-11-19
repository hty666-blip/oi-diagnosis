"""管理写操作串行化，并将变更与脱敏审计放在同一事务中。"""
from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import AdminAudit, SchoolClass, User
from app.infra.db_lock import acquire_submission_lock


async def management_lock(session: AsyncSession):
    await acquire_submission_lock(session, -9223372036854775807)


async def admin_write(session: AsyncSession, user: User, request: Request):
    await management_lock(session)
    await session.refresh(user)
    if not user.enabled or user.role != "admin" or user.token_version != request.state.token_version:
        raise HTTPException(401, "管理权限已变化，请重新登录")


async def training_write(session: AsyncSession, user: User):
    """与改班名/归档/转交串行，避免新草稿保留已失效的班名或负责人。"""
    version = user.token_version
    await management_lock(session)
    await session.refresh(user)
    if not user.enabled or user.role not in ("admin", "teacher") or version != user.token_version:
        raise HTTPException(401, "账号权限已变化，请重新登录")


def user_out(user: User):
    return {key: getattr(user, key) for key in (
        "id", "username", "display_name", "role", "class_name", "class_id", "enabled",
        "token_version", "must_change_password",
    )}


def audit(session: AsyncSession, actor: User, action: str, target: str, before: dict, after: dict):
    session.add(AdminAudit(actor_id=actor.id, action=action, target=target, before=before, after=after))


async def get_user(session: AsyncSession, user_id: int, expected_version: int | None = None):
    row = await session.get(User, user_id, populate_existing=True)
    if row is None:
        raise HTTPException(404, "账号不存在")
    if expected_version is not None and row.token_version != expected_version:
        raise HTTPException(409, "账号已被修改，请刷新后重试")
    return row


async def get_class(session: AsyncSession, class_id: int | None, *, active=True):
    if class_id is None:
        return None
    row = await session.get(SchoolClass, class_id, populate_existing=True)
    if row is None or (active and row.archived):
        raise HTTPException(422, "班级不存在或已归档")
    return row


async def protect_last_admin(session: AsyncSession, row: User, role: str, enabled: bool):
    if row.role == "admin" and row.enabled and (role != "admin" or not enabled):
        other = await session.scalar(select(User.id).where(User.role == "admin", User.enabled.is_(True), User.id != row.id).limit(1))
        if other is None:
            raise HTTPException(409, "必须保留至少一个启用的管理员账号")
