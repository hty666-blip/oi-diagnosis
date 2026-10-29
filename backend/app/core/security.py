"""密码哈希与 JWT。

不引入 passlib/bcrypt：pbkdf2_hmac 是标准库自带，安全足够，装依赖少一个坑。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import time

import jwt

from app.core.config import settings

PBKDF2_ROUNDS = 120_000
MAX_PASSWORD_LENGTH = 1024
MAX_TOKEN_LENGTH = 8192
MAX_USER_ID = 2**63 - 1
USER_ROLES = frozenset({"student", "teacher", "admin"})
HMAC_KEY_BYTES = {"HS256": 32, "HS384": 48, "HS512": 64}
SUBJECT_PATTERN = re.compile(r"[1-9][0-9]{0,18}\Z")


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return "pbkdf2$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    if not isinstance(password, str) or len(password) > MAX_PASSWORD_LENGTH:
        return False
    if not isinstance(stored, str) or len(stored) > 256:
        return False
    try:
        algo, salt_b64, dk_b64 = stored.split("$")
        if algo != "pbkdf2":
            return False
        salt = base64.b64decode(salt_b64, validate=True)
        expect = base64.b64decode(dk_b64, validate=True)
        if len(expect) != hashlib.sha256().digest_size:
            return False
        got = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
        return hmac.compare_digest(expect, got)
    except Exception:  # noqa: BLE001
        return False


# 不存在的账号仍做相同轮数的密码校验；该值绝不作为任何账号的凭据。
DUMMY_PASSWORD_HASH = hash_password("invalid-login-password", salt=b"oi-login-dummy01")


def validate_security_settings() -> None:
    """非开发环境拒绝默认凭据和调试配置，错误信息不包含秘密值。"""
    if settings.jwt_alg not in HMAC_KEY_BYTES:
        raise ValueError("JWT_ALG 必须使用受支持的 HMAC 签名算法")
    if settings.app_env.strip().lower() == "dev":
        return
    if len(settings.jwt_secret.strip().encode("utf-8")) < HMAC_KEY_BYTES[settings.jwt_alg]:
        raise ValueError("非开发环境必须设置足够长度的独立 JWT_SECRET")
    if settings.jwt_secret in {"dev-only-change-me", "change-me", "sk-replace-me"}:
        raise ValueError("非开发环境不能使用示例 JWT_SECRET")
    if settings.app_debug:
        raise ValueError("非开发环境必须关闭 APP_DEBUG")


def create_access_token(user_id: int, role: str, username: str) -> str:
    if type(user_id) is not int or not 0 < user_id <= MAX_USER_ID:
        raise ValueError("无效的账号编号")
    if role not in USER_ROLES:
        raise ValueError("无效的账号角色")
    if not isinstance(username, str) or not username or len(username) > 64:
        raise ValueError("无效的账号名称")
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "role": role,
        "username": username,
        "iat": now,
        "exp": now + settings.access_token_expire_minutes * 60,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_alg)


def decode_token(token: str) -> dict | None:
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH:
        return None
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_alg],
            options={"require": ["sub", "role", "username", "iat", "exp"]},
        )
        subject = payload["sub"]
        if not isinstance(subject, str) or not SUBJECT_PATTERN.fullmatch(subject):
            return None
        if int(subject) > MAX_USER_ID or payload["role"] not in USER_ROLES:
            return None
        if type(payload["iat"]) is not int or type(payload["exp"]) is not int:
            return None
        if payload["iat"] < 0 or payload["exp"] <= payload["iat"]:
            return None
        username = payload["username"]
        if not isinstance(username, str) or not username or len(username) > 64:
            return None
        username.encode("utf-8")
        return payload
    except (jwt.PyJWTError, TypeError, ValueError, UnicodeError):
        return None
