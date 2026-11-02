"""登录与当前用户。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import CurrentUser, SessionDep
from app.core.config import settings
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    MAX_PASSWORD_LENGTH,
    USER_ROLES,
    create_access_token,
    hash_password,
    password_needs_rehash,
    verify_password,
)
from app.core.text_validation import StorageText
from app.domain.models import User
from app.services.admin_service import audit, management_lock

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: StorageText = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

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
    class_id: int | None
    must_change_password: bool


def _out(u: User) -> UserOut:
    return UserOut(id=u.id, username=u.username, display_name=u.display_name,
                   role=u.role, class_name=u.class_name, class_id=u.class_id,
                   must_change_password=u.must_change_password)


@router.post("/login")
async def login(payload: LoginIn, session: SessionDep) -> dict:
    try:
        user = (
            await session.execute(select(User).where(User.username == payload.username))
        ).scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="登录服务暂时不可用，请稍后重试") from exc
    password_matches = verify_password(
        payload.password, user.password_hash if user is not None else DUMMY_PASSWORD_HASH
    )
    if user is None or not password_matches or user.role not in USER_ROLES or not user.enabled:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
        try:
            await session.commit()
        except SQLAlchemyError as exc:
            await session.rollback()
            raise HTTPException(status_code=503, detail="登录服务暂时不可用，请稍后重试") from exc
    token = create_access_token(user.id, user.role, user.username, user.token_version)
    return {"access_token": token, "token_type": "bearer", "user": _out(user).model_dump()}


@router.get("/me")
async def me(user: CurrentUser) -> UserOut:
    return _out(user)


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: StorageText = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    new_password: StorageText = Field(min_length=8, max_length=128)


@router.post("/password")
async def change_password(payload: PasswordChange, session: SessionDep, user: CurrentUser):
    version = user.token_version
    await management_lock(session)
    await session.refresh(user)
    if not user.enabled or user.token_version != version:
        raise HTTPException(401, "登录已撤销，请重新登录")
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(422, "当前密码不正确")
    if payload.current_password == payload.new_password:
        raise HTTPException(422, "新密码不能与当前密码相同")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    user.token_version += 1
    audit(session, user, "account.change_password", f"user:{user.id}", {}, {"must_change_password": False})
    await session.commit()
    return {"access_token": create_access_token(user.id, user.role, user.username, user.token_version),
            "user": _out(user).model_dump()}


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
