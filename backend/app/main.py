"""应用入口：认证、教学业务及可恢复的后台工作器。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.errors import request_validation_error_handler
from app.api.v1 import (
    admin,
    admin_problems,
    admin_runtime,
    admin_written,
    auth,
    card,
    catalog,
    operations,
    reviews,
    students,
    submissions,
    system,
    trainings,
    tutor,
    written_questions,
)
from app.capabilities.sandbox import SandboxError
from app.core.config import settings
from app.core.security import validate_security_settings
from app.infra.db import dispose_engine, ping
from app.maintenance_worker import MaintenanceWorker
from app.worker import JudgeWorker


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_security_settings()
    worker = asyncio.create_task(JudgeWorker().run_forever()) if settings.judge_worker_enabled else None
    maintenance = asyncio.create_task(MaintenanceWorker().run_forever()) if settings.maintenance_worker_enabled else None
    try:
        yield
    finally:
        if worker is not None:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        if maintenance is not None:
            maintenance.cancel()
            with suppress(asyncio.CancelledError):
                await maintenance
        await dispose_engine()


app = FastAPI(
    title="OI Diagnosis",
    version="0.1.0",
    description="青少年信息学竞赛错题诊断与答疑系统",
    lifespan=lifespan,
)
app.add_exception_handler(RequestValidationError, request_validation_error_handler)

app.include_router(submissions.router, prefix="/api/v1")
app.include_router(system.router, prefix="/api/v1")
app.include_router(tutor.router, prefix="/api/v1")
app.include_router(catalog.router, prefix="/api/v1")
app.include_router(card.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
app.include_router(admin_problems.router, prefix="/api/v1")
app.include_router(admin_runtime.router, prefix="/api/v1")
app.include_router(admin_written.router, prefix="/api/v1")
app.include_router(reviews.router, prefix="/api/v1")
app.include_router(operations.router, prefix="/api/v1")
app.include_router(trainings.router, prefix="/api/v1")
app.include_router(students.router, prefix="/api/v1")
app.include_router(written_questions.router, prefix="/api/v1")


@app.exception_handler(SandboxError)
async def unavailable_problem(request: Request, exc: SandboxError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "题目版本暂不可用，请联系管理员核对题面与测试快照"})


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





