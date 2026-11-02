"""模型配置不回显密钥，先检查再启用；业务调用跨进程读取同一版本。"""
import base64
import hashlib
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.text_validation import StorageText
from app.domain.models import AICallEvent, AIConfiguration, AIConfigurationCheck
from app.gateway.llm import LLMClient


def cipher():
    key = hashlib.sha256(("oi-ai-configuration-v1:" + settings.jwt_secret).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_key(value: str) -> str:
    return cipher().encrypt(value.encode()).decode()


def decrypt_key(value: str) -> str:
    return cipher().decrypt(value.encode()).decode() if value else ""


async def configuration(session):
    row = await session.get(AIConfiguration, 1, populate_existing=True)
    if row is None:
        return {"version": 0, "enabled": settings.llm_enabled, "base_url": settings.llm_api_base,
                "model": settings.llm_model_reasoning or settings.llm_model_fast, "key": settings.llm_api_key,
                "source": "environment", "checked_at": None}
    try:
        key = decrypt_key(row.encrypted_key)
    except (InvalidToken, UnicodeError, ValueError):
        key = ""
    return {"version": row.version, "enabled": row.enabled and settings.llm_enabled,
            "base_url": row.base_url, "model": row.model, "key": key, "source": "database", "checked_at": row.checked_at}


async def runtime_client(session, *, factory=None):
    if not settings.llm_enabled:
        return None
    config = await configuration(session)
    if not config["enabled"]:
        return None
    factory = factory or LLMClient
    if config["source"] == "environment":
        return factory()
    return factory(base_url=config["base_url"], api_key=config["key"], model=config["model"])


async def observe_call(session: AsyncSession, client, feature: str, *, used: bool, reason=""):
    call = getattr(client, "last_call", None)
    if call is None and used:
        return  # 测试/自定义客户端未提供遥测，不能伪造一次外部调用。
    error = (reason or getattr(call, "error", "") or "")[:64]
    event = AICallEvent(feature=feature, model=str(getattr(call, "model", ""))[:100], called=call is not None,
                        used_model=used, error=error,
                        latency_ms=getattr(call, "latency_ms", None), prompt_tokens=getattr(call, "prompt_tokens", None),
                        completion_tokens=getattr(call, "completion_tokens", None))
    try:
        # 独立事务保留已发生的调用；业务保存失败不应把计量记录抹掉。
        async with AsyncSession(bind=session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint") as log:
            log.add(event)
            await log.commit()
    except Exception:
        pass  # 计量暂不可写不能中断学生答疑；没有记录不等于没有费用。


class AICandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(strict=True, ge=0)
    base_url: StorageText = Field(min_length=1, max_length=500)
    model: StorageText = Field(min_length=1, max_length=100)
    api_key: StorageText = Field(default="", max_length=4096)

    @model_validator(mode="after")
    def validate_address(self):
        self.base_url, self.model = self.base_url.strip().rstrip("/"), self.model.strip()
        url = urlsplit(self.base_url)
        if (url.scheme not in ("http", "https") or not url.hostname or url.username or url.password
            or url.query or url.fragment or not self.model):
            raise ValueError("请填写不含登录凭据、查询参数的模型服务地址和模型名称")
        if url.scheme == "http" and (settings.app_env != "dev" or url.hostname not in ("localhost", "127.0.0.1", "::1")):
            raise ValueError("模型地址需使用 HTTPS；本地开发仅允许回环 HTTP")
        _ = url.port
        if any(ord(c) < 32 for c in self.api_key):
            raise ValueError("密钥不能包含控制字符")
        return self


async def candidate_client(session, payload: AICandidate):
    if not settings.llm_enabled:
        raise HTTPException(409, "部署配置已关闭外部模型调用，请先由部署人员开启")
    current = await configuration(session)
    if current["version"] != payload.expected_version:
        raise HTTPException(409, "模型配置已变化，请刷新后重试")
    if not payload.api_key and payload.base_url != current["base_url"].rstrip("/"):
        raise HTTPException(422, "更换服务地址时请重新填写对应密钥")
    key = payload.api_key or current["key"]
    if not key:
        raise HTTPException(422, "请填写模型密钥")
    return LLMClient(base_url=payload.base_url, api_key=key, model=payload.model, timeout=15), key


async def status(session):
    config = await configuration(session)
    configured = bool(config["key"] and config["base_url"] and config["model"])
    latest = await session.scalar(select(AIConfigurationCheck).order_by(AIConfigurationCheck.created_at.desc()).limit(1))
    events = (await session.scalars(select(AICallEvent).order_by(AICallEvent.id.desc()).limit(100))).all()
    def total(field):
        values = [getattr(e, field) for e in events if getattr(e, field) is not None]
        return sum(values) if values else None
    return {key: config[key] for key in ("version", "enabled", "base_url", "model", "source", "checked_at")} | {
        "environment_allowed": settings.llm_enabled, "key_configured": bool(config["key"]), "configured": configured,
        "last_check": {"ok": latest.ok, "error": latest.error, "created_at": latest.created_at,
                       "base_url": latest.base_url, "model": latest.model, "latency_ms": latest.latency_ms} if latest else None,
        "events": [{"id": e.id, "feature": e.feature, "model": e.model, "called": e.called,
                    "used_model": e.used_model, "error": e.error, "latency_ms": e.latency_ms,
                    "prompt_tokens": e.prompt_tokens, "completion_tokens": e.completion_tokens, "created_at": e.created_at} for e in events],
        "usage": {"observed_records": len(events), "external_calls": sum(e.called for e in events),
                  "prompt_tokens": total("prompt_tokens"), "completion_tokens": total("completion_tokens"),
                  "unknown_usage": sum(e.called and (e.prompt_tokens is None or e.completion_tokens is None) for e in events)},
        "observed_at": await session.scalar(select(func.clock_timestamp()))}


async def save_check(session, user, payload, client, key, ok):
    call = client.last_call
    row = AIConfigurationCheck(id=str(uuid4()), actor_id=user.id, base_version=payload.expected_version,
        base_url=payload.base_url, model=payload.model, encrypted_key=encrypt_key(key) if ok else "",
        ok=ok, error="" if ok else str(getattr(call, "error", "check_failed") or "check_failed")[:64],
        latency_ms=getattr(call, "latency_ms", None), expires_at=datetime.now(UTC) + timedelta(minutes=10))
    session.add(row)
    await session.flush()
    return row
