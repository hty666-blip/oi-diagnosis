"""系统自检接口：给前端/运维一个"现在能不能用模型"的开关。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import SessionDep, TeacherUser
from app.services.runtime_ai import configuration, observe_call, runtime_client

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/llm")
async def llm_status(session: SessionDep) -> dict:
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
    ok, msg = await cli.ping()
    await observe_call(session, cli, "check", used=ok)
    return {"ok": ok, "message": msg, "model": cli.model}
