"""题库导入校验和不可变版本安装，网页与命令行共用。"""
import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.capabilities.sandbox import SandboxError
from app.capabilities.test_suite import PACKAGE_ROOT, JudgeSuite, load_package, load_suite
from app.core.text_validation import StorageText
from app.domain.models import Problem, ProblemVersion, Submission, TrainingProblem
from app.services.problem_versions import FIELDS, capture_current, problem_data, version_digest


def utf8(value: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("测试文本必须是有效 UTF-8") from exc
    return value


JudgeText = Annotated[str, AfterValidator(utf8)]


class SampleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: JudgeText = Field(max_length=65536)
    output: JudgeText = Field(max_length=65536)


class CaseInput(SampleInput):
    input: JudgeText = Field(max_length=1_000_000)
    output: JudgeText = Field(max_length=1_000_000)
    name: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9_-]*$")


class ProblemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pid: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    title: StorageText = Field(min_length=1, max_length=200)
    statement_md: StorageText = Field(min_length=1, max_length=200000)
    samples: list[SampleInput] = Field(default_factory=list, max_length=20)
    difficulty: StorageText = Field(default="", max_length=32)
    tags: list[Annotated[StorageText, Field(min_length=1, max_length=64)]] = Field(default_factory=list, max_length=30)
    knowledge_points: list[Annotated[StorageText, Field(min_length=1, max_length=64)]] = Field(default_factory=list, max_length=30)
    package_level: Literal["A", "B", "C", "D"] = "C"
    source: StorageText = Field(min_length=1, max_length=32)
    source_url: StorageText = Field(default="", max_length=500)
    license_note: StorageText = Field(min_length=1, max_length=4000)
    reviewer: StorageText = Field(default="", max_length=100)
    time_limit_ms: int = Field(default=2000, strict=True, ge=1, le=30000)
    memory_limit_mb: int = Field(default=256, strict=True, ge=1, le=2048)
    cases: list[CaseInput] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_package(self):
        self.pid = self.pid.upper()
        for key in ("title", "statement_md", "source", "license_note"):
            if not getattr(self, key).strip():
                raise ValueError(f"{key} 不能为空白")
        if self.license_note.strip().startswith("请填写"):
            raise ValueError("请用实际许可说明替换模板占位内容")
        if self.source_url:
            url = urlsplit(self.source_url)
            if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
                raise ValueError("来源链接需为不含登录凭据的 HTTP/HTTPS 地址")
        if self.package_level in ("A", "B"):
            if not self.reviewer.strip() or self.reviewer.strip().startswith("请填写") or len(self.cases) < (3 if self.package_level == "A" else 1):
                raise ValueError("A/B 级需审核人及足量正式测试：A 至少 3 个，B 至少 1 个")
        elif self.cases:
            raise ValueError("有正式测试时请选择 A/B 等级；C/D 只使用公开样例")
        names = [c.name or f"case{i}" for i, c in enumerate(self.cases, 1)]
        if len(names) != len(set(names)):
            raise ValueError("正式测试名称不能重复")
        if len(self.model_dump_json().encode("utf-8")) > 2_000_000:
            raise ValueError("单题内容不得超过 2 MB")
        return self

    def record(self) -> dict:
        data = {key: getattr(self, key) for key in FIELDS if key not in ("samples", "has_testdata")}
        data.update(samples=[s.model_dump() for s in self.samples], has_testdata=self.package_level in ("A", "B"))
        cases = [{**c.model_dump(), "name": c.name or f"case{i}"} for i, c in enumerate(self.cases, 1)] if data["has_testdata"] else data["samples"]
        scope = "package" if data["has_testdata"] else "samples"
        raw = json.dumps({"pid": self.pid, "statement": self.statement_md, "scope": scope, "cases": cases,
                          "time_limit_ms": self.time_limit_ms, "memory_limit_mb": self.memory_limit_mb},
                         ensure_ascii=False, sort_keys=True).encode("utf-8")
        suite = asdict(JudgeSuite(scope, cases, hashlib.sha256(raw).hexdigest(), self.time_limit_ms, self.memory_limit_mb))
        return {"data": data, "suite": suite, "reviewer": self.reviewer,
                "digest": version_digest(data, suite, self.reviewer)}


async def adopt_legacy(session: AsyncSession, problem: Problem):
    if problem.revision:
        return
    try:
        old = await capture_current(session, problem)
    except SandboxError:
        data = problem_data(problem)
        number = (await session.scalar(select(func.max(ProblemVersion.revision)).where(ProblemVersion.problem_id == problem.id)) or 0) + 1
        old = ProblemVersion(problem_id=problem.id, revision=number, data=data, suite=None,
                             digest=version_digest(data, None))
        session.add(old)
        await session.flush()
    problem.revision = old.revision
    for model in (Submission, TrainingProblem):
        await session.execute(update(model).where(model.problem_id == problem.id, model.problem_version_id.is_(None)).values(problem_version_id=old.id))


async def install_version(session: AsyncSession, record: dict, actor_id: int | None = None):
    """调用方持有管理事务锁；返回是否真正追加版本，不自动上架。"""
    data = record["data"]
    if version_digest(data, record["suite"], record["reviewer"]) != record["digest"]:
        raise SandboxError("导入预览完整性校验失败")
    row = await session.scalar(select(Problem).where(Problem.pid == data["pid"]))
    if row:
        await adopt_legacy(session, row)
        current = await session.scalar(select(ProblemVersion).where(ProblemVersion.problem_id == row.id, ProblemVersion.revision == row.revision))
        if current and current.digest == record["digest"]:
            return row, current, False
        number = (await session.scalar(select(func.max(ProblemVersion.revision)).where(ProblemVersion.problem_id == row.id)) or 0) + 1
    else:
        row = Problem(**data, is_active=False)
        session.add(row)
        await session.flush()
        number = 1
    version = ProblemVersion(problem_id=row.id, revision=number, data=data, suite=record["suite"],
                             reviewer=record["reviewer"], digest=record["digest"], created_by=actor_id)
    session.add(version)
    for key, value in data.items():
        setattr(row, key, value)
    row.revision, row.is_active = number, False
    row.catalog_version += 1
    await session.flush()
    return row, version, True


def local_package(pid: str, root: Path = PACKAGE_ROOT) -> ProblemInput:
    meta, cases = load_package(pid, root)
    directory = (root / pid).resolve()
    if not (directory / "statement.md").resolve().is_relative_to(directory):
        raise ValueError("题面路径超出题包")
    if (directory / "statement.md").stat().st_size > 800000:
        raise ValueError("题面文件过大")
    statement = (directory / "statement.md").read_text(encoding="utf-8-sig")
    blocks = re.findall(r"```[^\n]*\n(.*?)```", statement.split("## 样例", 1)[-1].split("## 说明", 1)[0], re.S)
    if len(blocks) % 2:
        raise ValueError("公开样例输入输出不成对")
    return ProblemInput(pid=pid, title=meta.get("title", ""), statement_md=statement,
                        samples=[{"input": blocks[i], "output": blocks[i + 1]} for i in range(0, len(blocks), 2)],
                        package_level=meta["package_level"], source="self-authored", license_note=meta.get("license", ""),
                        reviewer=meta.get("reviewer", ""), knowledge_points=meta.get("knowledge_points", []),
                        difficulty=str(meta.get("difficulty", "")), cases=cases,
                        time_limit_ms=meta.get("time_limit_ms", 2000), memory_limit_mb=meta.get("memory_limit_mb", 256))


def legacy_record(data: dict) -> dict:
    """可信 CLI 的旧元数据格式；网页导入仍强制来源/许可及人工确认。"""
    obj = SimpleNamespace(**data)
    suite = asdict(load_suite(obj))
    reviewer = str(load_package(obj.pid)[0].get("reviewer", "")) if data["has_testdata"] and data["package_level"] in ("A", "B") else ""
    return {"data": data, "suite": suite, "reviewer": reviewer, "digest": version_digest(data, suite, reviewer)}
