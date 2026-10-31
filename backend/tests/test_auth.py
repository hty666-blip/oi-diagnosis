"""登录输入、密码兼容升级与令牌校验。"""

from __future__ import annotations

import base64
import hashlib

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.api.v1.auth import LoginIn, demo_accounts, login
from app.core.config import settings
from app.core.security import (
    LEGACY_PBKDF2_ROUNDS,
    create_access_token,
    decode_token,
    hash_password,
    password_needs_rehash,
    verify_password,
)
from app.domain.models import User


def _legacy_hash(password: str) -> str:
    salt = b"legacy-test-salt"
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt, LEGACY_PBKDF2_ROUNDS
    )
    return "$".join((
        "pbkdf2",
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    ))


class _Result:
    def __init__(self, user: User | None):
        self.user = user

    def scalar_one_or_none(self) -> User | None:
        return self.user


class _Session:
    def __init__(self, user: User | None):
        self.user = user
        self.committed = False

    async def execute(self, _query) -> _Result:
        return _Result(self.user)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        pass


class _BrokenSession(_Session):
    async def execute(self, _query) -> _Result:
        raise SQLAlchemyError("database offline")


def test_login_input_is_normalized_and_bounded():
    payload = LoginIn(username="  Student01  ", password="123456")
    assert payload.username == "student01"
    with pytest.raises(ValidationError):
        LoginIn(username="包含空格", password="123456")
    with pytest.raises(ValidationError):
        LoginIn(username="student01", password="123")


def test_password_hash_roundtrip_and_legacy_detection():
    current = hash_password("correct-password", salt=b"current-test-salt")
    legacy = _legacy_hash("correct-password")
    assert verify_password("correct-password", current)
    assert not verify_password("wrong-password", current)
    assert not password_needs_rehash(current)
    assert verify_password("correct-password", legacy)
    assert password_needs_rehash(legacy)


def test_token_roundtrip_and_tamper_rejection():
    token = create_access_token(7, "student", "student01")
    payload = decode_token(token)
    assert payload is not None
    assert payload["sub"] == "7"
    assert payload["username"] == "student01"
    assert decode_token(token + "x") is None


@pytest.mark.asyncio
async def test_successful_login_upgrades_legacy_password():
    user = User(
        id=7,
        username="student01",
        display_name="学生",
        role="student",
        password_hash=_legacy_hash("123456"),
        class_name="一班",
    )
    session = _Session(user)
    result = await login(LoginIn(username="STUDENT01", password="123456"), session)
    assert result["user"]["username"] == "student01"
    assert decode_token(result["access_token"])["sub"] == "7"
    assert session.committed
    assert not password_needs_rehash(user.password_hash)


@pytest.mark.asyncio
async def test_unknown_user_has_generic_error():
    with pytest.raises(HTTPException) as caught:
        await login(LoginIn(username="missing", password="123456"), _Session(None))
    assert caught.value.status_code == 401
    assert caught.value.detail == "用户名或密码错误"


@pytest.mark.asyncio
async def test_database_failure_is_reported_as_service_unavailable():
    with pytest.raises(HTTPException) as caught:
        await login(LoginIn(username="student01", password="123456"), _BrokenSession(None))
    assert caught.value.status_code == 503
    assert caught.value.detail == "登录服务暂时不可用，请稍后重试"


@pytest.mark.asyncio
async def test_demo_accounts_are_hidden_outside_development(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "prod")
    with pytest.raises(HTTPException) as caught:
        await demo_accounts()
    assert caught.value.status_code == 404
