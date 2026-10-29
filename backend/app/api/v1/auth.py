"""登录与当前用户。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.core.config import settings
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    MAX_PASSWORD_LENGTH,
    USER_ROLES,
    create_access_token,
    verify_password,
)
from app.core.text_validation import StorageText
from app.domain.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: StorageText = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)

    @field_validator("username", "password")
    @classmethod
    def require_valid_text(cls, value: str) -> str:
        try:
            value.encode("utf-8")
        except UnicodeError as exc:
            raise ValueError("登录字段包含无效字符") from exc
        return value


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    class_name: str


def _out(u: User) -> UserOut:
    return UserOut(id=u.id, username=u.username, display_name=u.display_name,
                   role=u.role, class_name=u.class_name)


@router.post("/login")
async def login(payload: LoginIn, session: SessionDep) -> dict:
    user = (
        await session.execute(select(User).where(User.username == payload.username))
    ).scalar_one_or_none()
    password_matches = verify_password(
        payload.password, user.password_hash if user is not None else DUMMY_PASSWORD_HASH
    )
    if user is None or not password_matches or user.role not in USER_ROLES:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_access_token(user.id, user.role, user.username)
    return {"access_token": token, "token_type": "bearer", "user": _out(user).model_dump()}


@router.get("/me")
async def me(user: CurrentUser) -> UserOut:
    return _out(user)


@router.get("/demo-accounts")
async def demo_accounts() -> list[dict]:
    """仅明确开发环境提供演示密码；其他环境保留正常登录入口。"""
    if settings.app_env.strip().lower() != "dev":
        return []
    return [
        {"username": "student01", "password": "123456", "role": "student", "name": "林小满"},
        {"username": "student02", "password": "123456", "role": "student", "name": "陈子昂"},
        {"username": "teacher01", "password": "123456", "role": "teacher", "name": "王老师"},
        {"username": "admin", "password": "123456", "role": "admin", "name": "管理员"},
    ]
