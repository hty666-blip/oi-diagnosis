"""密码哈希与 JWT。

不引入 passlib/bcrypt：pbkdf2_hmac 是标准库自带，安全足够，装依赖少一个坑。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time

import jwt

from app.core.config import settings

PBKDF2_ROUNDS = 310_000
LEGACY_PBKDF2_ROUNDS = 120_000
PASSWORD_ALGORITHM = "pbkdf2_sha256"
TOKEN_ISSUER = "oi-diagnosis"
TOKEN_AUDIENCE = "oi-web"


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return "$".join((
        PASSWORD_ALGORITHM,
        str(PBKDF2_ROUNDS),
        base64.b64encode(salt).decode(),
        base64.b64encode(dk).decode(),
    ))


def _password_parts(stored: str) -> tuple[int, bytes, bytes]:
    parts = stored.split("$")
    if len(parts) == 3 and parts[0] == "pbkdf2":
        rounds, salt_b64, digest_b64 = LEGACY_PBKDF2_ROUNDS, parts[1], parts[2]
    elif len(parts) == 4 and parts[0] == PASSWORD_ALGORITHM:
        rounds, salt_b64, digest_b64 = int(parts[1]), parts[2], parts[3]
    else:
        raise ValueError("不支持的密码哈希格式")
    if not 100_000 <= rounds <= 1_000_000:
        raise ValueError("密码哈希迭代次数无效")
    return rounds, base64.b64decode(salt_b64), base64.b64decode(digest_b64)


def verify_password(password: str, stored: str) -> bool:
    try:
        rounds, salt, expect = _password_parts(stored)
        got = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
        return hmac.compare_digest(expect, got)
    except Exception:  # noqa: BLE001
        return False


def password_needs_rehash(stored: str) -> bool:
    try:
        rounds, _, _ = _password_parts(stored)
        return not stored.startswith(f"{PASSWORD_ALGORITHM}$") or rounds < PBKDF2_ROUNDS
    except Exception:  # noqa: BLE001
        return True


def create_access_token(user_id: int, role: str, username: str) -> str:
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "role": role,
        "username": username,
        "iss": TOKEN_ISSUER,
        "aud": TOKEN_AUDIENCE,
        "jti": secrets.token_urlsafe(16),
        "iat": now,
        "exp": now + settings.access_token_expire_minutes * 60,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_alg)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_alg],
            audience=TOKEN_AUDIENCE,
            issuer=TOKEN_ISSUER,
        )
    except jwt.PyJWTError:
        return None
