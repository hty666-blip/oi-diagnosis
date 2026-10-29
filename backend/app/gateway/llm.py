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
    prompt_tokens: int = 0
    completion_tokens: int = 0
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
        return bool(self.base_url and self.api_key)

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
                    # 不打印任何 header / key，只记状态码与错误体前 200 字
                    logger.warning("LLM HTTP %s: %s", resp.status_code, resp.text[:200])
                    self.last_call = LLMCall(model=payload["model"], latency_ms=latency, ok=False,
                                             error=f"http_{resp.status_code}")
                    if resp.status_code in (401, 403):
                        return None   # key 无效，重试没意义
                    continue

                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                usage = data.get("usage") or {}
                self.last_call = LLMCall(
                    model=data.get("model", payload["model"]), latency_ms=latency,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                )
                return _parse_json(content)
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
                    logger.warning("LLM(text) HTTP %s: %s", resp.status_code, resp.text[:160])
                    self.last_call = LLMCall(model=payload["model"], latency_ms=latency,
                                             ok=False, error=f"http_{resp.status_code}")
                    if resp.status_code in (401, 403):
                        return None
                    continue
                data = resp.json()
                usage = data.get("usage") or {}
                self.last_call = LLMCall(
                    model=data.get("model", payload["model"]), latency_ms=latency,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                )
                text = (data["choices"][0]["message"]["content"] or "").strip()
                return text or None
            except Exception as e:  # noqa: BLE001
                self.last_call = LLMCall(model=payload["model"], latency_ms=0, ok=False,
                                         error=type(e).__name__)
                logger.warning("LLM(text) 失败(%s/%s): %s", attempt, retries, type(e).__name__)
        return None

    async def ping(self) -> tuple[bool, str]:
        """最小连通性检查：只让模型回一个词，几乎不耗 token。"""
        if not self.available:
            return False, "未配置 base_url 或 api_key"
        try:
            async with httpx.AsyncClient(timeout=20, transport=self.transport) as cli:
                resp = await cli.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"model": self.model,
                          "messages": [{"role": "user", "content": "回复一个词：ok"}],
                          "max_tokens": 5, "temperature": 0},
                )
            if resp.status_code != 200:
                return False, f"HTTP {resp.status_code}: {resp.text[:120]}"
            data = resp.json()
            return True, str(data["choices"][0]["message"]["content"])[:40]
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"


def _parse_json(content: str) -> dict | None:
    if not content:
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


