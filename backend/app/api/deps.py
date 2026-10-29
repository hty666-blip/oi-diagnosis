"""认证依赖：从 Authorization 头解析用户，并提供角色守卫。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.domain.models import User
from app.infra.db import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    payload = decode_token(authorization.split(" ", 1)[1].strip())
    if not payload:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="无效的登录凭据") from exc
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*roles: str):
    async def _guard(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="没有权限访问该功能")
        return user

    return _guard


TeacherUser = Annotated[User, Depends(require_role("teacher", "admin"))]
StudentUser = Annotated[User, Depends(require_role("student", "teacher", "admin"))]
