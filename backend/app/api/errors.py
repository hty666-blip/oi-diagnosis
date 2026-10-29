"""请求校验错误只展示字段与原因，不回传请求值或验证上下文。"""

from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def safe_text(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    # JSON 可合法转义孤立 surrogate，但响应的 UTF-8 编码不接受它。
    return value.encode("utf-8", errors="replace").decode("utf-8")


def safe_location(value: object) -> list[str | int]:
    if not isinstance(value, (list, tuple)):
        return []
    return [part if type(part) is int else safe_text(part, "field") for part in value]


async def request_validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    details = []
    for error in exc.errors():
        if not isinstance(error, dict):
            continue
        details.append({
            "type": safe_text(error.get("type"), "value_error"),
            "loc": safe_location(error.get("loc")),
            "msg": safe_text(error.get("msg"), "请求字段无效，请检查后重试。"),
        })
    return JSONResponse(status_code=422, content={"detail": details})
