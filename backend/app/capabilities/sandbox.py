"""沙箱客户端：只做三件事——拼 /run 请求、控制超时、把返回映射成领域对象。

不在这里加业务逻辑（判题策略、错因归类都在上层）。

go-judge REST 约定：
- 时间单位是**纳秒**
- copyIn 传源码用 {"content": "..."}，传编译产物用 {"fileId": "..."}
- copyOutCached 可以让编译产物留在文件存储里，返回 fileIds，供后续 run 复用
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import quote

import httpx

from app.core.config import settings

CPP_STD = "c++17"
EXECUTION_STATUSES = {
    "Accepted", "Memory Limit Exceeded", "Time Limit Exceeded", "Output Limit Exceeded",
    "Non Zero Exit Status", "Nonzero Exit Status", "Signalled", "Dangerous Syscall",
}


@dataclass
class CommandResult:
    status: str                 # Accepted / Time Limit Exceeded / ...
    exit_code: int
    time_ns: int
    memory_bytes: int
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    file_ids: dict[str, str] = field(default_factory=dict)

    @property
    def time_ms(self) -> int:
        return int(self.time_ns / 1_000_000)

    @property
    def memory_kb(self) -> int:
        return int(self.memory_bytes / 1024)


@dataclass
class CompileResult:
    ok: bool
    stderr: str
    artifact_id: str | None = None      # go-judge fileId（编译产物）
    raw: CommandResult | None = None


class SandboxError(RuntimeError):
    pass


class SandboxClient:
    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        self.base_url = (base_url or settings.judge_url).rstrip("/")
        self.token = token if token is not None else settings.judge_token
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> SandboxClient:
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def start(self) -> None:
        if self._client is None:
            headers = {"X-Judge-Server-Token": self.token} if self.token else {}
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=settings.judge_timeout_s,
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ---------- 底层 ----------

    async def _run(self, payload: dict) -> CommandResult:
        await self.start()
        assert self._client is not None
        try:
            resp = await self._client.post("/run", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise SandboxError(f"judge 调用失败: {e}") from e

        try:
            data = resp.json()
            results = data if isinstance(data, list) else data["results"]
            if not isinstance(results, list) or len(results) != 1:
                raise ValueError("执行结果数量无效")
            item = results[0]
            if not isinstance(item, dict):
                raise ValueError("执行结果结构无效")
            status = item.get("status")
            if status not in EXECUTION_STATUSES or item.get("error") or item.get("fileError"):
                raise SandboxError(f"judge 未完成执行: {status}: {str(item.get('error', ''))[:1000]}")
            files = item.get("files") or {}
            file_ids = item.get("fileIds") or {}
            if (not isinstance(files, dict) or not isinstance(file_ids, dict)
                    or any(not isinstance(value, str) for value in [*files.values(), *file_ids.values()])):
                raise ValueError("执行输出结构无效")
            exit_code = item["exitStatus"]
            time_ns, memory_bytes = item.get("time", 0), item.get("memory", 0)
            if any(type(value) is not int for value in (exit_code, time_ns, memory_bytes)):
                raise ValueError("执行计量字段无效")
            if time_ns < 0 or memory_bytes < 0:
                raise ValueError("执行计量字段不能为负数")
            if status == "Accepted" and "stdout" not in files:
                raise ValueError("执行结果缺少标准输出")
        except (ValueError, TypeError, KeyError, IndexError) as e:
            raise SandboxError("judge 返回无效执行结果") from e
        # go-judge REST 返回原始文本，不能猜测或按 base64 解码学生输出。
        return CommandResult(
            status="Non Zero Exit Status" if status == "Nonzero Exit Status" else status,
            exit_code=exit_code,
            time_ns=time_ns,
            memory_bytes=memory_bytes,
            stdout=files.get("stdout", ""),
            stderr=files.get("stderr", ""),
            error=item.get("error") or None,
            file_ids=file_ids,
        )

    # ---------- 对外 ----------

    async def compile(
        self,
        code: str,
        *,
        filename: str = "main.cpp",
        sanitize: bool = False,
        cpu_limit_ns: int = 10_000_000_000,
    ) -> CompileResult:
        flags = ["-O2", f"-std={CPP_STD}", "-Wall", "-o", "main", filename]
        if sanitize:
            # ASan 会让内存占用涨 2~3 倍，调用方要相应放宽 memory_limit
            flags = ["-O1", "-g", f"-std={CPP_STD}", "-fsanitize=address,undefined",
                     "-fno-omit-frame-pointer", "-o", "main", filename]

        payload = {
            "cmd": [{
                "args": ["/usr/bin/g++", *flags],
                "env": ["PATH=/usr/bin:/bin", "LANG=C.UTF-8"],
                "files": [
                    {"content": ""},
                    {"name": "stdout", "max": 8 * 1024},
                    {"name": "stderr", "max": 128 * 1024},
                ],
                "cpuLimit": cpu_limit_ns,
                "clockLimit": cpu_limit_ns + 5_000_000_000,
                "memoryLimit": 1024 * 1024 * 1024,
                "procLimit": 64,
                "copyIn": {filename: {"content": code}},
                "copyOut": ["stdout", "stderr"],
                # 编译失败时 main 不存在是正常现象，不应触发 File Error。
                "copyOutCached": ["main?"],
            }],
        }
        res = await self._run(payload)
        ok = res.exit_code == 0 and res.status == "Accepted"
        if ok and not res.file_ids.get("main"):
            raise SandboxError("judge 编译成功但缺少可执行产物")
        if not ok and res.status not in ("Non Zero Exit Status", "Accepted"):
            raise SandboxError(f"judge 编译验证未完成: {res.status}")
        return CompileResult(
            ok=ok,
            stderr=res.stderr,
            artifact_id=res.file_ids.get("main"),
            raw=res,
        )

    async def run(
        self,
        artifact_id: str | None = None,
        *,
        stdin: str = "",
        time_limit_ms: int = 2000,
        memory_limit_mb: int = 512,
        stdout_max: int = 64 * 1024,
        extra_args: list[str] | None = None,
    ) -> CommandResult:
        if not artifact_id:
            raise SandboxError("缺少编译产物 fileId")
        if time_limit_ms <= 0 or memory_limit_mb <= 0 or stdout_max <= 0:
            raise SandboxError("执行限制必须为正数")
        payload = {
            "cmd": [{
                "args": ["main", *(extra_args or [])],
                "env": ["PATH=/usr/bin:/bin"],
                "files": [
                    {"content": stdin},
                    {"name": "stdout", "max": stdout_max},
                    {"name": "stderr", "max": 32 * 1024},
                ],
                "cpuLimit": time_limit_ms * 1_000_000,
                "clockLimit": (time_limit_ms + 2000) * 1_000_000,
                "memoryLimit": memory_limit_mb * 1024 * 1024,
                "procLimit": 1,
                "copyIn": {"main": {"fileId": artifact_id}},
                "copyOut": ["stdout", "stderr"],
            }],
        }
        res = await self._run(payload)
        if len(res.stdout.encode("utf-8")) > stdout_max or len(res.stderr.encode("utf-8")) > 32 * 1024:
            res.status = "Output Limit Exceeded"
            res.stdout = res.stdout.encode("utf-8")[:stdout_max].decode("utf-8", "replace")
            res.stderr = res.stderr.encode("utf-8")[:32 * 1024].decode("utf-8", "replace")
        return res

    async def cleanup(self, artifact_id: str | None) -> None:
        if not artifact_id:
            return
        await self.start()
        assert self._client is not None
        try:
            response = await self._client.delete(f"/file/{quote(artifact_id, safe='')}")
            if response.status_code != 404:
                response.raise_for_status()
        except httpx.HTTPError as e:
            raise SandboxError("无法清理 judge 编译产物") from e

    async def compile_and_run(
        self,
        code: str,
        stdin: str = "",
        *,
        time_limit_ms: int = 2000,
        memory_limit_mb: int = 512,
    ) -> tuple[CompileResult, CommandResult | None]:
        """便捷入口：编译失败直接返回 (结果, None)。"""
        comp = await self.compile(code)
        if not comp.ok or not comp.artifact_id:
            return comp, None
        try:
            run = await self.run(
                comp.artifact_id, stdin=stdin,
                time_limit_ms=time_limit_ms, memory_limit_mb=memory_limit_mb,
            )
            return comp, run
        finally:
            await self.cleanup(comp.artifact_id)
