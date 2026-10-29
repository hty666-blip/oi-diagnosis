"""登录与当前用户。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.core.security import create_access_token, verify_password
from app.domain.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


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
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_access_token(user.id, user.role, user.username)
    return {"access_token": token, "token_type": "bearer", "user": _out(user).model_dump()}


@router.get("/me")
async def me(user: CurrentUser) -> UserOut:
    return _out(user)


@router.get("/demo-accounts")
async def demo_accounts() -> list[dict]:
    """演示用账号列表（仅本地/dev 环境使用；生产应关闭）。"""
    return [
        {"username": "student01", "password": "123456", "role": "student", "name": "林小满"},
        {"username": "student02", "password": "123456", "role": "student", "name": "陈子昂"},
        {"username": "teacher01", "password": "123456", "role": "teacher", "name": "王老师"},
        {"username": "admin", "password": "123456", "role": "admin", "name": "管理员"},
    ]
