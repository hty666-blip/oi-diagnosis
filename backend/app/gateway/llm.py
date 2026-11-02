"""模型网关：唯一的模型出口。

为什么不让业务代码直接调 SDK：
- 需要统一超时/重试/降级（模型挂了不能把诊断带崩）
- 需要统一计费与日志（不打印 key，只打印 token 数）
- 需要可替换（DeepSeek / 通义 / 自建，都是 OpenAI 兼容协议）

当前只实现了 `chat_json`：诊断官需要的是结构化输出，不是自由文本。
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


@dataclass
class LLMCall:
    model: str
    latency_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    ok: bool = True
    error: str = ""
    meta: dict = field(default_factory=dict)


class LLMClient:
    def __init__(self, *, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None, timeout: float = 40.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = (base_url or settings.llm_api_base or "").rstrip("/")
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.model = model or settings.llm_model_reasoning or settings.llm_model_fast
        self.timeout = timeout
        self.transport = transport          # 测试注入 MockTransport
        self.last_call: LLMCall | None = None

    @property
    def available(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    async def chat_json(self, system: str, user: str, *, model: str | None = None,
                        max_tokens: int = 900, retries: int = 1) -> dict | None:
        """返回解析后的 JSON；任何失败都返回 None（调用方走降级）。"""
        if not self.available:
            logger.info("LLM 未配置，跳过调用")
            return None

        payload = {
            "model": model or self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        for attempt in range(retries + 1):
            t0 = time.time()
            try:
                async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as cli:
                    resp = await cli.post(f"{self.base_url}/chat/completions",
                                          json=payload, headers=headers)
                latency = int((time.time() - t0) * 1000)
                if resp.status_code >= 400:
                    # 服务端错误体可能回显请求或密钥，只记录状态码。
                    logger.warning("LLM HTTP %s", resp.status_code)
                    self.last_call = LLMCall(model=payload["model"], latency_ms=latency, ok=False,
                                             error=f"http_{resp.status_code}")
                    if resp.status_code in (401, 403):
                        return None   # key 无效，重试没意义
                    continue

                data = resp.json()
                content, usage = _response_content(data)
                parsed = _parse_json(content)
                self.last_call = LLMCall(
                    model=payload["model"], latency_ms=latency,
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    ok=parsed is not None, error="" if parsed is not None else "invalid_json",
                )
                return parsed
            except Exception as e:  # noqa: BLE001
                latency = int((time.time() - t0) * 1000)
                self.last_call = LLMCall(model=payload["model"], latency_ms=latency, ok=False,
                                         error=type(e).__name__)
                logger.warning("LLM 调用失败(%s/%s): %s", attempt, retries, type(e).__name__)
        return None

    async def chat_text(self, system: str, user: str, *, model: str | None = None,
                        max_tokens: int = 500, retries: int = 1,
                        temperature: float = 0.6) -> str | None:
        """自由文本输出（导师讲解用）。失败返回 None，由调用方走模板兜底。"""
        if not self.available:
            return None
        payload = {
            "model": model or self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        for attempt in range(retries + 1):
            t0 = time.time()
            try:
                async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as cli:
                    resp = await cli.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
                latency = int((time.time() - t0) * 1000)
                if resp.status_code >= 400:
                    logger.warning("LLM(text) HTTP %s", resp.status_code)
                    self.last_call = LLMCall(model=payload["model"], latency_ms=latency,
                                             ok=False, error=f"http_{resp.status_code}")
                    if resp.status_code in (401, 403):
                        return None
                    continue
                data = resp.json()
                text, usage = _response_content(data)
                self.last_call = LLMCall(
                    model=payload["model"], latency_ms=latency,
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                )
                return text.strip()
            except Exception as e:  # noqa: BLE001
                self.last_call = LLMCall(model=payload["model"], latency_ms=int((time.time() - t0) * 1000), ok=False,
                                         error=type(e).__name__)
                logger.warning("LLM(text) 失败(%s/%s): %s", attempt, retries, type(e).__name__)
        return None

    async def ping(self) -> tuple[bool, str]:
        """最小连通性检查：只让模型回一个词，几乎不耗 token。"""
        text = await self.chat_text("只回复 OK。", "连通性检查", max_tokens=5, retries=0, temperature=0)
        return (True, "已收到有效模型回复") if text else (False, "模型检查失败，请核对配置或稍后重试")


def _parse_json(content: str) -> dict | None:
    if not isinstance(content, str) or not content:
        return None
    text = FENCE.sub("", content).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        # 兜底：截取第一个 { 到最后一个 }
        i, j = text.find("{"), text.rfind("}")
        if 0 <= i < j:
            try:
                return json.loads(text[i:j + 1])
            except json.JSONDecodeError:
                return None
    return None


def _response_content(data: object) -> tuple[str, dict]:
    """兼容协议只接受可用的文本，拒绝把对象、列表和空内容当回复。"""
    if not isinstance(data, dict):
        raise ValueError("invalid_response")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError("invalid_choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("invalid_message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("invalid_content")
    usage = data.get("usage") or {}
    if not isinstance(usage, dict) or any(
        usage.get(key) is not None and (type(usage[key]) is not int or not 0 <= usage[key] <= 2**63 - 1)
        for key in ("prompt_tokens", "completion_tokens")
    ):
        raise ValueError("invalid_usage")
    return content, usage


