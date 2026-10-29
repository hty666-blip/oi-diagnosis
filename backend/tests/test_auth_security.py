import time

import jwt
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.deps import get_current_user
from app.api.v1.auth import LoginIn, demo_accounts
from app.core.config import settings
from app.core.security import (
    MAX_USER_ID,
    create_access_token,
    decode_token,
    hash_password,
    validate_security_settings,
    verify_password,
)


@pytest.fixture
def signing_settings(monkeypatch):
    monkeypatch.setattr(settings, "jwt_alg", "HS256")
    monkeypatch.setattr(settings, "jwt_secret", "auth-test-key-is-at-least-thirty-two-bytes")
    monkeypatch.setattr(settings, "access_token_expire_minutes", 15)


def signed_claims(**updates):
    now = int(time.time())
    payload = {"sub": "1", "role": "student", "username": "中文账号", "iat": now, "exp": now + 600}
    payload.update(updates)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_alg)


def test_current_password_hashes_and_chinese_usernames_remain_compatible(signing_settings):
    stored = hash_password("正确口令·一二三", salt=b"legacy-salt")
    assert verify_password("正确口令·一二三", stored)
    assert not verify_password("错误口令", stored)
    token = create_access_token(MAX_USER_ID, "student", "中文账号")
    assert decode_token(token)["username"] == "中文账号"


@pytest.mark.parametrize("missing", ["sub", "role", "username", "iat", "exp"])
def test_signed_token_requires_existing_session_claims(signing_settings, missing):
    payload = jwt.decode(signed_claims(), settings.jwt_secret, algorithms=[settings.jwt_alg])
    del payload[missing]
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_alg)
    assert decode_token(token) is None


@pytest.mark.parametrize("subject", ["0", "-1", "+1", "01", "１", str(MAX_USER_ID + 1), "9" * 100, 1, None])
def test_malformed_or_out_of_range_subject_is_rejected_before_database(signing_settings, subject):
    assert decode_token(signed_claims(sub=subject)) is None


@pytest.mark.parametrize("updates", [
    {"role": "superadmin"}, {"role": {}}, {"username": None}, {"username": "x" * 65},
    {"iat": "1"}, {"iat": True}, {"exp": str(int(time.time()) + 600)},
    {"exp": int(time.time()) - 1}, {"iat": int(time.time()) + 3600},
])
def test_bad_claim_types_and_lifetimes_are_rejected(signing_settings, updates):
    assert decode_token(signed_claims(**updates)) is None


def test_tampered_and_oversized_tokens_are_rejected(signing_settings):
    other_key = jwt.encode({"sub": "1"}, "different-test-key-at-least-thirty-two-bytes", algorithm="HS256")
    assert decode_token(other_key) is None
    assert decode_token("x" * 8193) is None
    assert decode_token("") is None


@pytest.mark.parametrize("payload", [
    {"username": "x" * 65, "password": "pass"},
    {"username": "同学", "password": "x" * 1025},
    {"username": "同学", "password": ""},
    {"username": "\ud800", "password": "pass"},
    {"username": "同学", "password": "\ud800"},
])
def test_login_bounds_invalid_input_before_query_or_hash(payload):
    with pytest.raises(ValidationError):
        LoginIn(**payload)


@pytest.mark.parametrize("environment", ["prod", "production", "staging", "test"])
async def test_non_development_environment_never_exposes_demo_passwords(monkeypatch, environment):
    monkeypatch.setattr(settings, "app_env", environment)
    assert await demo_accounts() == []


async def test_development_demo_still_works(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "dev")
    assert {row["role"] for row in await demo_accounts()} == {"student", "teacher", "admin"}


def test_non_development_security_settings_require_real_key_and_no_debug(monkeypatch, signing_settings):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "app_debug", False)
    validate_security_settings()
    monkeypatch.setattr(settings, "jwt_secret", "dev-only-change-me")
    with pytest.raises(ValueError, match="JWT_SECRET"):
        validate_security_settings()
    monkeypatch.setattr(settings, "jwt_secret", "auth-test-key-is-at-least-thirty-two-bytes")
    monkeypatch.setattr(settings, "app_debug", True)
    with pytest.raises(ValueError, match="APP_DEBUG"):
        validate_security_settings()


@pytest.mark.parametrize("algorithm,key_bytes", [("HS256", 32), ("HS384", 48), ("HS512", 64)])
def test_production_hmac_keys_meet_the_algorithms_minimum(monkeypatch, algorithm, key_bytes):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "app_debug", False)
    monkeypatch.setattr(settings, "jwt_alg", algorithm)
    monkeypatch.setattr(settings, "jwt_secret", "x" * key_bytes)
    validate_security_settings()
    monkeypatch.setattr(settings, "jwt_secret", "x" * (key_bytes - 1))
    with pytest.raises(ValueError, match="JWT_SECRET"):
        validate_security_settings()


def test_whitespace_key_and_unsupported_algorithm_are_not_safe_configuration(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "app_debug", False)
    monkeypatch.setattr(settings, "jwt_alg", "HS256")
    monkeypatch.setattr(settings, "jwt_secret", " " * 64)
    with pytest.raises(ValueError, match="JWT_SECRET"):
        validate_security_settings()
    monkeypatch.setattr(settings, "jwt_alg", "none")
    with pytest.raises(ValueError, match="JWT_ALG"):
        validate_security_settings()


async def test_invalid_subject_never_reaches_database(signing_settings):
    class NeverDatabase:
        async def get(self, *args):
            raise AssertionError("非法 subject 不应送入数据库")

    with pytest.raises(HTTPException) as error:
        await get_current_user(NeverDatabase(), "Bearer " + signed_claims(sub=str(MAX_USER_ID + 1)))
    assert error.value.status_code == 401


async def test_wrong_password_and_missing_account_share_a_generic_failure(api_context, monkeypatch):
    ctx = api_context
    ctx.users.student.password_hash = hash_password("测试正确密码")
    await ctx.session.commit()
    import app.api.v1.auth as auth_module

    checked = []
    original = auth_module.verify_password

    def spy(password, stored):
        checked.append(stored)
        return original(password, stored)

    monkeypatch.setattr(auth_module, "verify_password", spy)
    wrong = await ctx.client.post("/api/v1/auth/login", json={
        "username": ctx.users.student.username, "password": "错误密码",
    })
    missing = await ctx.client.post("/api/v1/auth/login", json={
        "username": "不存在的中文同学", "password": "错误密码",
    })
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json() == missing.json()
    assert len(checked) == 2
    assert checked[0] != checked[1]


async def test_database_role_controls_access_instead_of_stale_token_role(api_context):
    ctx = api_context
    token_headers = ctx.headers(ctx.users.student)
    ctx.users.student.role = "unknown"
    await ctx.session.commit()
    response = await ctx.client.get("/api/v1/auth/me", headers=token_headers)
    assert response.status_code == 401


async def test_invalid_login_payload_returns_422_without_hitting_password_verification(api_context, monkeypatch):
    ctx = api_context

    def forbidden(*args):
        raise AssertionError("超长登录内容应在哈希之前拒绝")

    monkeypatch.setattr("app.api.v1.auth.verify_password", forbidden)
    response = await ctx.client.post("/api/v1/auth/login", json={"username": "同学", "password": "x" * 1025})
    assert response.status_code == 422
