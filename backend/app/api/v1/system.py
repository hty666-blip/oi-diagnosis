"""系统自检接口：给前端/运维一个"现在能不能用模型"的开关。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import TeacherUser
from app.core.config import settings
from app.gateway.llm import LLMClient

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/llm")
async def llm_status() -> dict:
    cli = LLMClient()
    return {
        "enabled": settings.llm_enabled,
        "available": settings.llm_enabled and cli.available,
        "base_url": cli.base_url,
        "model": cli.model,
        "key_configured": bool(settings.llm_api_key),
    }


@router.post("/llm/ping")
async def llm_ping(user: TeacherUser) -> dict:
    if not settings.llm_enabled:
        raise HTTPException(status_code=409, detail="当前环境已关闭外部模型调用")
    cli = LLMClient()
    ok, msg = await cli.ping()
    return {"ok": ok, "message": msg, "model": cli.model}
