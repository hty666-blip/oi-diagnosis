"""登录与当前用户。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import CurrentUser, SessionDep
from app.core.config import settings
from app.core.security import (
    create_access_token,
    hash_password,
    password_needs_rehash,
    verify_password,
)
from app.domain.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=6, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    class_name: str


def _out(u: User) -> UserOut:
    return UserOut(id=u.id, username=u.username, display_name=u.display_name,
                   role=u.role, class_name=u.class_name)


_DUMMY_PASSWORD_HASH = hash_password("not-a-real-password", salt=b"\0" * 16)


@router.post("/login")
async def login(payload: LoginIn, session: SessionDep) -> dict:
    try:
        user = (
            await session.execute(select(User).where(User.username == payload.username))
        ).scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="登录服务暂时不可用，请稍后重试") from exc
    stored_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
    password_ok = verify_password(payload.password, stored_hash)
    if user is None or not password_ok:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
        try:
            await session.commit()
        except SQLAlchemyError as exc:
            await session.rollback()
            raise HTTPException(status_code=503, detail="登录服务暂时不可用，请稍后重试") from exc
    token = create_access_token(user.id, user.role, user.username)
    return {"access_token": token, "token_type": "bearer", "user": _out(user).model_dump()}


@router.get("/me")
async def me(user: CurrentUser) -> UserOut:
    return _out(user)


@router.get("/demo-accounts")
async def demo_accounts() -> list[dict]:
    """演示用账号列表（仅本地/dev 环境使用；生产应关闭）。"""
    if settings.app_env not in {"dev", "test"}:
        raise HTTPException(status_code=404, detail="接口不存在")
    return [
        {"username": "student01", "password": "123456", "role": "student", "name": "林小满"},
        {"username": "student02", "password": "123456", "role": "student", "name": "陈子昂"},
        {"username": "teacher01", "password": "123456", "role": "teacher", "name": "王老师"},
        {"username": "admin", "password": "123456", "role": "admin", "name": "管理员"},
    ]
