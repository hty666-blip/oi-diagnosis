"""系统自检接口：给前端/运维一个"现在能不能用模型"的开关。"""

from __future__ import annotations

from hashlib import sha256

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep, TeacherUser
from app.domain.models import AdminAudit
from app.infra.db_lock import acquire_submission_lock
from app.infra.sql_types import after_seconds
from app.services.admin_service import audit
from app.services.runtime_ai import configuration, observe_call, runtime_client

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/llm")
async def llm_status(session: SessionDep, user: CurrentUser) -> dict:
    config = await configuration(session)
    return {
        "enabled": config["enabled"],
        "available": config["enabled"] and bool(config["key"] and config["base_url"] and config["model"]),
        "base_url": config["base_url"],
        "model": config["model"],
        "key_configured": bool(config["key"]),
    }


@router.post("/llm/ping")
async def llm_ping(user: TeacherUser, session: SessionDep) -> dict:
    cli = await runtime_client(session)
    if cli is None:
        raise HTTPException(status_code=409, detail="当前环境已关闭外部模型调用")
    version = user.token_version
    key = int.from_bytes(sha256(f"model-check:{user.id}".encode()).digest()[:8], "big", signed=True)
    await acquire_submission_lock(session, key)
    await session.refresh(user)
    if not user.enabled or user.role not in ("teacher", "admin") or user.token_version != version:
        raise HTTPException(401, "账号权限已变化，请重新登录")
    recent = await session.scalar(select(AdminAudit.id).where(
        AdminAudit.actor_id == user.id, AdminAudit.action == "ai.connectivity_check",
        AdminAudit.created_at > after_seconds(-30),
    ).limit(1))
    if recent is not None:
        raise HTTPException(429, "刚刚已发起模型检查，请等待30秒后再试。")
    # 短事务先预留频率窗口，网络调用期间不持有管理锁；多个 API 进程共享限制。
    audit(session, user, "ai.connectivity_check", f"user:{user.id}", {}, {"requested": True})
    await session.commit()
    ok, msg = await cli.ping()
    await observe_call(session, cli, "check", used=ok)
    return {"ok": ok, "message": msg, "model": cli.model}
