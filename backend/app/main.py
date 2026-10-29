"""应用入口。M0 只需要 /health 能起来。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1 import auth, card, catalog, submissions, system, tutor
from app.core.config import settings
from app.infra.db import dispose_engine, ping


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await dispose_engine()


app = FastAPI(
    title="OI Diagnosis",
    version="0.1.0",
    description="青少年信息学竞赛错题诊断与答疑系统",
    lifespan=lifespan,
)

app.include_router(submissions.router, prefix="/api/v1")
app.include_router(system.router, prefix="/api/v1")
app.include_router(tutor.router, prefix="/api/v1")
app.include_router(catalog.router, prefix="/api/v1")
app.include_router(card.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")


@app.get("/health", tags=["ops"])
async def health() -> dict:
    """健康检查：进程活着就返回 200；数据库状态单列，避免探活把 DB 算进去。"""
    db_ok = await ping()
    return {
        "status": "ok",
        "version": app.version,
        "env": settings.app_env,
        "db": "up" if db_ok else "down",
    }


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {"service": "oi-diagnosis", "docs": "/docs"}





