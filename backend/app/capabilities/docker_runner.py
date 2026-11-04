"""Docker 版执行器（开发环境兜底）。

为什么需要它：
    go-judge 依赖 clone(CLONE_INTO_CGROUP)，在本机 WSL2 内核（6.18）上稳定返回
    EAGAIN（`execve: start: /bin/echo: clone: resource temporarily unavailable`），
    换 v1.9.10 / 关闭 seccomp / cgroupns=host 均无效——这是环境限制，不是配置问题。

    生产环境（原生 Linux）仍用 go-judge；本机用 Docker 容器执行，接口保持一致，
    上层 SubmissionService 无感知。

安全边界（本机开发）：
    --network none、--memory、--cpus、--pids-limit、timeout、只读根文件系统、--rm。
    工作目录可写以保存编译产物；它仍只是本地演示执行器。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import uuid
from contextlib import suppress
from datetime import datetime
from pathlib import Path

from app.capabilities.languages import language_spec
from app.capabilities.sandbox import CommandResult, CompileResult, SandboxError

IMAGE = "oi-judge:dev"
RUN_ROOT = Path(__file__).resolve().parents[3] / "data/run"
DOCKER_FAILURE_CODES = {125, 126, 127}
OUTPUT_LIMIT_EXIT = -1001


class _OutputLimitReached(Exception):
    pass


class DockerRunner:
    def __init__(self, image: str = IMAGE, language: str = "cpp17") -> None:
        self.image = image
        self.last_state: dict | None = None
        self.set_language(language)
        self.run_root = RUN_ROOT.resolve()
        try:
            self.run_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise SandboxError("无法创建执行器工作根目录") from e

    def set_language(self, language: str) -> None:
        self.language = language
        self.spec = language_spec(language)

    def _workdir(self, artifact_id: str | Path) -> Path:
        """只接受执行器根目录下的直接 job 子目录，清理前也使用同一校验。"""
        work = Path(artifact_id).resolve()
        if work.parent != self.run_root or not work.name.startswith("job-"):
            raise SandboxError("编译产物目录不属于当前执行器")
        return work

    @staticmethod
    def _check_docker_failure(rc: int, stderr: str) -> None:
        if rc in DOCKER_FAILURE_CODES:
            raise SandboxError(f"Docker 执行环境不可用（{rc}）: {stderr[:1000]}")

    async def compile(self, code: str, *, filename: str | None = None,
                      sanitize: bool = False, cpu_limit_ns: int = 10_000_000_000) -> CompileResult:
        filename = filename or self.spec.filename
        if Path(filename).name != filename or filename in ("", ".", ".."):
            raise SandboxError("源码文件名无效")
        try:
            work = Path(tempfile.mkdtemp(prefix="job-", dir=str(self.run_root)))
            # 编译器和学生程序都以无特权 UID 运行，只开放本次独占目录。
            work.chmod(0o777)
        except OSError as e:
            raise SandboxError("无法准备编译工作目录") from e
        try:
            (work / filename).write_text(code, encoding="utf-8")
            cmd = self.spec.compile_command(filename, sanitize=sanitize)
            rc, out, err = await self._docker(work, cmd, timeout_s=15,
                                             stdout_max=128 * 1024, stderr_max=128 * 1024)
            self._check_docker_failure(rc, err)
            if rc == OUTPUT_LIMIT_EXIT:
                raise SandboxError("编译器输出超限，未完成编译验证")
            if rc == 1:
                await self.cleanup(str(work))
                return CompileResult(ok=False, stderr=err or out, raw=None)
            if rc != 0:
                raise SandboxError(f"编译过程异常退出（{rc}），未完成编译验证")
            if not (work / self.spec.artifact).is_file():
                raise SandboxError("编译器未返回可执行产物")
            return CompileResult(ok=True, stderr=err, artifact_id=str(work))
        except OSError as e:
            await self.cleanup(str(work))
            raise SandboxError("无法准备或读取编译文件") from e
        except BaseException:
            await self.cleanup(str(work))
            raise

    async def run(self, artifact_id: str | None = None, *, stdin: str = "",
                  time_limit_ms: int = 2000, memory_limit_mb: int = 512,
                  stdout_max: int = 64 * 1024, extra_args: list[str] | None = None) -> CommandResult:
        if not artifact_id:
            raise SandboxError("缺少编译产物目录")
        work = self._workdir(artifact_id)
        if not (work / self.spec.artifact).is_file():
            raise SandboxError("编译产物不存在")
        if time_limit_ms <= 0 or memory_limit_mb <= 0 or stdout_max <= 0:
            raise SandboxError("执行限制必须为正数")
        try:
            (work / "input.txt").write_text(stdin, encoding="utf-8")
        except OSError as e:
            raise SandboxError("无法准备执行输入") from e
        seconds = max(time_limit_ms / 1000.0, 0.5)
        exit_file = work / f".exit-{uuid.uuid4().hex}"
        try:
            exit_file.write_text("", encoding="ascii")
            exit_file.chmod(0o666)
        except OSError as exc:
            raise SandboxError("无法准备执行状态文件") from exc
        # timeout 和 Docker 有自己的保留退出码，不能直接使用学生程序的退出码。
        # 独立文件保留学生原始状态，内层只返回 0/1；参数不经 shell 字符串插值。
        program = [*self.spec.run_command(memory_limit_mb), *(extra_args or [])]
        script = ('result_file=$1; shift; "$@" < input.txt; student_rc=$?; '
                  'printf "%s" "$student_rc" > "$result_file" || exit 125; '
                  '[ "$student_rc" -eq 0 ]')
        guard = ('artifact=$1; runtime=$2; shift 2; '
                 '[ -f "$artifact" ] && [ -x "$runtime" ] || exit 126; exec "$@"')
        cmd = ["bash", "-c", guard, "oi-run", self.spec.artifact, program[0],
               "timeout", "-k", "1s", f"{seconds:.3f}s", "bash", "-c", script,
               "oi-student", exit_file.name, *program]
        student_rc = None
        self.last_state = None
        try:
            rc, out, err = await self._docker(work, cmd, timeout_s=time_limit_ms / 1000.0 + 5,
                                              memory_mb=memory_limit_mb, stdout_max=stdout_max,
                                              stderr_max=32 * 1024, status_file=exit_file, inspect_state=True)
            if rc in (0, 1) and len(out.encode("utf-8")) <= stdout_max and len(err.encode("utf-8")) <= 32 * 1024:
                try:
                    if exit_file.is_symlink():
                        raise ValueError("退出状态文件不能是链接")
                    with exit_file.open("rb") as source:
                        raw = source.read(4)
                    if not raw.isdigit() or not 0 <= int(raw) <= 255:
                        raise ValueError("退出状态无效")
                    student_rc = int(raw)
                    if (rc == 0) != (student_rc == 0):
                        raise ValueError("退出状态不一致")
                except (OSError, ValueError) as exc:
                    raise SandboxError("无法确认学生程序退出状态") from exc
        finally:
            with suppress(OSError):
                exit_file.unlink(missing_ok=True)
        self._check_docker_failure(rc, err)
        status = "Accepted"
        if (rc == OUTPUT_LIMIT_EXIT or len(out.encode("utf-8")) > stdout_max
                or len(err.encode("utf-8")) > 32 * 1024):
            status = "Output Limit Exceeded"
        elif self.last_state is not None and self.last_state.get("OOMKilled") is True:
            status = "Memory Limit Exceeded"
        elif rc == 124:
            status = "Time Limit Exceeded"
        elif rc == 137:
            # 137 也可能是外部 SIGKILL，不能据此确认发生了内存超限。
            status = "Signalled"
        elif rc != 0:
            status = "Non Zero Exit Status"
        elapsed_ns = 0
        if self.last_state:
            try:
                started = datetime.fromisoformat(self.last_state["StartedAt"].replace("Z", "+00:00"))
                finished = datetime.fromisoformat(self.last_state["FinishedAt"].replace("Z", "+00:00"))
                elapsed_ns = max(0, int((finished - started).total_seconds() * 1_000_000_000))
            except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
                pass  # 未取得可信测量时保持未知，不用宿主启动耗时冒充程序 CPU 时间。
        return CommandResult(
            status=status, exit_code=student_rc if student_rc is not None else rc,
            time_ns=elapsed_ns, memory_bytes=0, time_kind="container_wall",
            stdout=out.encode("utf-8")[:stdout_max].decode("utf-8", "replace"),
            stderr=err.encode("utf-8")[:32 * 1024].decode("utf-8", "replace"),
        )

    async def compile_and_run(self, code: str, stdin: str = "", *,
                              time_limit_ms: int = 2000, memory_limit_mb: int = 512,
                              cleanup: bool = True):
        comp = await self.compile(code)
        if not comp.ok or not comp.artifact_id:
            return comp, None
        try:
            run = await self.run(comp.artifact_id, stdin=stdin,
                                 time_limit_ms=time_limit_ms, memory_limit_mb=memory_limit_mb)
            return comp, run
        finally:
            if cleanup:
                await self.cleanup(comp.artifact_id)

    async def cleanup(self, artifact_id: str | None) -> None:
        if artifact_id:
            work = self._workdir(artifact_id)
            try:
                shutil.rmtree(work)
            except FileNotFoundError:
                pass
            except OSError as e:
                raise SandboxError("无法清理执行器工作目录") from e

    async def _stop(self, proc: asyncio.subprocess.Process, container_name: str) -> None:
        if proc.returncode is None:
            with suppress(ProcessLookupError):
                proc.kill()
        with suppress(TimeoutError, ProcessLookupError):
            await asyncio.wait_for(proc.wait(), timeout=2)
        # 杀死 docker CLI 不保证容器停止；只移除本次执行生成的唯一名称。
        try:
            remove = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", container_name,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(remove.wait(), timeout=5)
            except TimeoutError:
                remove.kill()
                with suppress(TimeoutError, ProcessLookupError):
                    await asyncio.wait_for(remove.wait(), timeout=2)
        except OSError:
            pass  # 原执行错误仍由调用方报告，清理失败不能变成学生代码错误。

    async def _docker(self, work: Path, cmd: list[str], *, timeout_s: float,
                      memory_mb: int = 1024, stdout_max: int = 128 * 1024,
                      stderr_max: int = 128 * 1024, status_file: Path | None = None,
                      inspect_state: bool = False) -> tuple[int, str, str]:
        path = str(self._workdir(work))
        container_name = f"oi-run-{uuid.uuid4().hex}"
        args = [
            "docker", "run", "--name", container_name,
            *([] if inspect_state else ["--rm"]),
            "--network", "none",                 # 断网
            "--memory", f"{memory_mb}m",         # 内存上限
            "--cpus", "1",                       # 单核
            "--pids-limit", "64",                # 防 fork 炸弹
            "--read-only",                       # 根文件系统只读
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777",
            "--user", "65534:65534",
            "--ulimit", "fsize=16777216:16777216",
            "--ulimit", "core=0:0",
            "--cap-drop", "ALL",                 # 丢掉所有 capability
            "--security-opt", "no-new-privileges",
            "-v", f"{path}:/w" + (":ro" if status_file is not None else ""),
            *(["-v", f"{status_file}:/w/{status_file.name}:rw"] if status_file is not None else []),
            "-w", "/w",
            "--entrypoint", cmd[0],
            self.image,
            *cmd[1:],
        ]
        if status_file is not None and (status_file.parent.resolve() != Path(path)
                or status_file.is_symlink() or not status_file.name.startswith(".exit-")):
            raise SandboxError("执行状态文件不属于当前任务")
        proc = None
        tasks = []
        out_b, err_b = bytearray(), bytearray()

        async def collect(stream, target: bytearray, limit: int) -> None:
            while chunk := await stream.read(8192):
                remaining = limit - len(target)
                target.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    raise _OutputLimitReached

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            tasks = [asyncio.create_task(collect(proc.stdout, out_b, stdout_max)),
                     asyncio.create_task(collect(proc.stderr, err_b, stderr_max)),
                     asyncio.create_task(proc.wait())]
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout_s)
        except _OutputLimitReached:
            await self._stop(proc, container_name)
            return OUTPUT_LIMIT_EXIT, out_b.decode("utf-8", "replace"), err_b.decode("utf-8", "replace")
        except TimeoutError as e:
            if proc is not None:
                await self._stop(proc, container_name)
            raise SandboxError("执行器超时") from e
        except asyncio.CancelledError:
            if proc is not None:
                await self._stop(proc, container_name)
            raise
        except FileNotFoundError as e:
            raise SandboxError("找不到 docker 命令") from e
        except OSError as e:
            if proc is not None:
                await self._stop(proc, container_name)
            raise SandboxError("无法启动或读取 Docker 执行器") from e
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        if inspect_state:
            inspect_proc = None
            try:
                inspect_proc = await asyncio.create_subprocess_exec(
                    "docker", "inspect", "--format", "{{json .State}}", container_name,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                raw, _ = await asyncio.wait_for(inspect_proc.communicate(), timeout=5)
                state = json.loads(raw)
                if inspect_proc.returncode != 0 or not isinstance(state, dict) or type(state.get("OOMKilled")) is not bool:
                    raise ValueError("invalid_container_state")
                self.last_state = state
            except (OSError, ValueError, TimeoutError) as exc:
                raise SandboxError("无法确认容器执行状态，本次验证未完成") from exc
            finally:
                if inspect_proc is not None and inspect_proc.returncode is None:
                    with suppress(ProcessLookupError):
                        inspect_proc.kill()
                    with suppress(TimeoutError, ProcessLookupError):
                        await asyncio.wait_for(inspect_proc.wait(), timeout=2)
                await self._stop(proc, container_name)
        return proc.returncode or 0, out_b.decode("utf-8", "replace"), err_b.decode("utf-8", "replace")
