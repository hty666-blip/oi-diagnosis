"""提交与判题用例：Triage 分诊 -> 取证 -> 判定 -> 证据落库。

流程（架构 §6 + 错因体系 v2）：
    RECEIVED -> TRIAGE(P0/P1) -> COMPILING -> RUNNING -> READY
                     │
                     └─ 命中 P0/P1 也要留证据，但结论以确定性规则为准

原则：确定性可判的（全角符号、编译报错映射）绝不让模型猜；
      诊断层只消费证据，不直接看源码。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.capabilities.ce_classify import classify_runtime
from app.capabilities.docker_runner import DockerRunner
from app.capabilities.sandbox import CommandResult, CompileResult, SandboxClient, SandboxError
from app.capabilities.test_suite import JudgeSuite, load_suite
from app.capabilities.triage import TriageFinding, TriageResult, triage
from app.core.config import settings
from app.domain.models import Diagnosis, Evidence, Problem, Submission

# 改动判题/诊断逻辑时必须递增：它是缓存键的一部分（同码同题会直接复用结果）
PIPELINE_VER = "v4"


def build_runner():
    """生产走 go-judge；本机 WSL 内核不兼容 go-judge，用 Docker 容器执行兜底。"""
    return SandboxClient() if settings.judge_backend == "gojudge" else DockerRunner()


def normalize_code(code: str) -> str:
    """仅统一换行，保留 BOM、原始行列及字符串中的空白供取证。"""
    return code.replace("\r\n", "\n").replace("\r", "\n")


def code_digest(code: str) -> str:
    return hashlib.sha256(normalize_code(code).encode("utf-8")).hexdigest()


def outputs_equal(actual: str, expected: str) -> bool:
    """与 OJ 一致的宽松比较：忽略行尾空格与末尾多余空行。"""
    a = [ln.rstrip() for ln in actual.replace("\r\n", "\n").split("\n")]
    b = [ln.rstrip() for ln in expected.replace("\r\n", "\n").split("\n")]
    while a and a[-1] == "":
        a.pop()
    while b and b[-1] == "":
        b.pop()
    return a == b


@dataclass
class VerdictResult:
    verdict: str                 # AC/SAMPLE_PASS/NO_TESTS/CE/WA/TLE/MLE/RE
    passed: int = 0
    total: int = 0
    detail: dict | None = None
    scope: str = "samples"
    suite_digest: str = ""

    def as_dict(self) -> dict:
        return {"verdict": self.verdict, "passed": self.passed, "total": self.total,
                "scope": self.scope, "suite_digest": self.suite_digest}


RUNTIME_STATUS = {
    "Time Limit Exceeded": "TLE",
    "Memory Limit Exceeded": "MLE",
    "Output Limit Exceeded": "OLE",
    "Non Zero Exit Status": "RE",
    "Signalled": "RE",
    "Dangerous Syscall": "RE",
}


def decide_verdict(comp: CompileResult, runs: list[tuple[dict, CommandResult]], *,
                   scope: str = "samples") -> VerdictResult:
    """只在非空正式测试全部通过时给 AC；样例通过不证明程序正确。"""
    if not comp.ok:
        return VerdictResult(verdict="CE", passed=0, total=len(runs), scope=scope)

    passed = 0
    first_bad: str | None = None
    for sample, res in runs:
        if res.status != "Accepted":
            if res.status not in RUNTIME_STATUS:
                raise SandboxError(f"执行器返回异常状态：{res.status}")
            first_bad = first_bad or RUNTIME_STATUS[res.status]
            continue
        if res.exit_code != 0:
            first_bad = first_bad or "RE"
            continue
        if not outputs_equal(res.stdout, sample.get("output", "")):
            first_bad = first_bad or "WA"
            continue
        passed += 1

    if passed == len(runs) and runs:
        return VerdictResult("AC" if scope == "package" else "SAMPLE_PASS",
                             passed, len(runs), scope=scope)
    if not runs:
        return VerdictResult("NO_TESTS", 0, 0, {"note": "no_tests"}, scope=scope)
    return VerdictResult(first_bad or "WA", passed, len(runs), scope=scope)


class SubmissionService:
    def __init__(self, session: AsyncSession, sandbox=None) -> None:
        self.session = session
        self.sandbox = sandbox or build_runner()

    async def create(self, problem_pid: str, code: str, student_id: int = 0) -> Submission:
        problem = (
            await self.session.execute(select(Problem).where(Problem.pid == problem_pid))
        ).scalar_one_or_none()
        if problem is None:
            raise ValueError(f"题目不存在: {problem_pid}")

        normalized = normalize_code(code)
        if not normalized.strip():
            raise ValueError("代码不能为空")
        digest = code_digest(normalized)
        suite = load_suite(problem)
        # 题面、数据或资源限制变化后旧结果不可复用；保持 pipeline_ver 在原字段长度内。
        pipeline_ver = f"{PIPELINE_VER}:{suite.digest[:24]}"

        existing = (
            await self.session.execute(
                select(Submission).where(
                    Submission.problem_id == problem.id,
                    Submission.code_hash == digest,
                    Submission.student_id == student_id,
                    Submission.pipeline_ver == pipeline_ver,
                    Submission.stage == "READY",
                )
                .order_by(Submission.id.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        sub = Submission(
            problem_id=problem.id,
            student_id=student_id,
            code=normalized,
            code_hash=digest,
            pipeline_ver=pipeline_ver,
            stage="RECEIVED",
        )
        self.session.add(sub)
        await self.session.commit()
        await self.session.refresh(sub)
        return sub

    async def run(self, submission: Submission) -> VerdictResult:
        """失败保持为系统错误，且无论成败都释放执行器资源。"""
        self._artifact_id = None
        try:
            return await self._run(submission)
        except SandboxError:
            submission.stage = "SANDBOX_ERROR"
            submission.verdict = None
            submission.tests_passed = None
            await self.session.commit()
            raise
        finally:
            artifact_id = getattr(self, "_artifact_id", None)
            cleanup = getattr(self.sandbox, "cleanup", None)
            if artifact_id and cleanup:
                await cleanup(artifact_id)
            close = getattr(self.sandbox, "close", None)
            if close:
                await close()

    async def _run(self, submission: Submission) -> VerdictResult:
        problem = (
            await self.session.execute(select(Problem).where(Problem.id == submission.problem_id))
        ).scalar_one()
        suite = load_suite(problem)
        submission.pipeline_ver = f"{PIPELINE_VER}:{suite.digest[:24]}"

        # 1) Triage 前置：先做确定性的字符扫描
        pre = triage(submission.code)
        await self._add_evidence(submission.id, "static_alert", {
            "kind": "surface_lint",
            "stage": "pre_compile",
            "route": pre.route,
            "count": len(pre.findings),
            "findings": [f.as_dict() for f in pre.findings],
        })

        submission.stage = "COMPILING"
        await self.session.commit()

        # 2) 编译
        comp = await self.sandbox.compile(submission.code)
        self._artifact_id = comp.artifact_id

        await self._add_evidence(submission.id, "compile_log", {
            "ok": comp.ok,
            "stderr": comp.stderr[:20000],
        })

        if not comp.ok:
            tr = triage(submission.code, compile_stderr=comp.stderr, verdict="CE")
            result = decide_verdict(comp, [], scope=suite.scope)
            await self._finalize(submission, result, tr, suite)
            return result

        # 3) 运行本次固定的测试集，记录输入以便重现失败。
        submission.stage = "RUNNING"
        await self.session.commit()

        runs: list[tuple[dict, CommandResult]] = []
        for idx, sample in enumerate(suite.cases, start=1):
            res = await self.sandbox.run(
                comp.artifact_id,
                stdin=sample.get("input", ""),
                time_limit_ms=suite.time_limit_ms,
                memory_limit_mb=suite.memory_limit_mb,
            )
            runs.append((sample, res))
            await self._add_evidence(submission.id, "run_trace", {
                "case": idx,
                "scope": suite.scope,
                "suite_digest": suite.digest,
                "input": sample["input"][:8000],
                "status": res.status,
                "exit_code": res.exit_code,
                "time_ms": res.time_ms,
                "memory_kb": res.memory_kb,
                "stdout": res.stdout[:4000],
                "stderr": res.stderr[:4000],
                "expected": str(sample.get("output", ""))[:4000],
            })

        result = decide_verdict(comp, runs, scope=suite.scope)
        result.suite_digest = suite.digest

        # 4) 运行期错因（RE/TLE/MLE 才有意义）
        tr = triage(submission.code, verdict=result.verdict)
        if result.verdict in ("RE", "TLE", "MLE") and runs:
            bad = next((r for _, r in runs if r.status != "Accepted"), runs[-1][1])
            for f in classify_runtime(bad.stderr, result.verdict).findings:
                tr.findings.append(TriageFinding(
                    p=f.nature, code=f.code, message=f.message, hint=f.hint,
                    confidence=f.confidence,
                ))
            if tr.findings:
                tr.summary = f"{tr.findings[0].message} {tr.findings[0].hint}".strip()

        await self._finalize(submission, result, tr, suite)
        return result

    async def _finalize(self, submission: Submission, result: VerdictResult,
                        tr: TriageResult, suite: JudgeSuite) -> None:
        submission.verdict = result.verdict
        result.suite_digest = suite.digest
        submission.tests_passed = {"passed": result.passed, "total": result.total,
                                   "scope": suite.scope, "suite_digest": suite.digest}
        submission.stage = "READY"

        mechanism = [
            {"code": f.code, "conf": f.confidence, "line": f.line}
            for f in tr.findings if f.code.startswith("M")
        ]
        nature = [
            {"p": f.p, "code": f.code, "line": f.line, "col": f.col,
             "message": f.message, "hint": f.hint, "autofix": f.autofix}
            for f in tr.findings
        ]

        diag = (
            await self.session.execute(
                select(Diagnosis).where(Diagnosis.submission_id == submission.id)
            )
        ).scalar_one_or_none() or Diagnosis(submission_id=submission.id)

        diag.mechanism = mechanism
        diag.nature = nature
        diag.narrative = tr.summary[:4000]
        if not tr.findings:
            diag.narrative = {
                "AC": "已通过本题当前版本的正式测试。",
                "SAMPLE_PASS": "公开样例已通过，尚未运行正式测试，不能确认程序正确。",
                "NO_TESTS": "代码已编译，本题没有可用测试数据，结果尚未验证。",
            }.get(result.verdict, diag.narrative)
        diag.status = "inconclusive" if result.verdict in ("SAMPLE_PASS", "NO_TESTS") else "draft"
        diag.model_meta = {
            "route": tr.route,
            "autofixable": tr.autofixable,
            "pipeline_ver": PIPELINE_VER,
        }
        diag.degraded = tr.notes
        self.session.add(diag)
        await self.session.commit()

    async def _add_evidence(self, submission_id: int, kind: str, payload: dict) -> Evidence:
        import json
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()[:32]
        ev = Evidence(submission_id=submission_id, type=kind, payload=payload, digest=digest)
        self.session.add(ev)
        await self.session.commit()
        return ev

