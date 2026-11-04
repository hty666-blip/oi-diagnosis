"""题面和测试集快照；判题、辅导和历史展示不跟随最新题库漂移。"""
import hashlib
import json
from dataclasses import asdict
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.capabilities.sandbox import SandboxError
from app.capabilities.test_suite import JudgeSuite, load_suite
from app.domain.models import Problem, ProblemVersion

FIELDS = ("pid", "title", "statement_md", "samples", "difficulty", "tags", "knowledge_points",
          "package_level", "has_testdata", "source", "source_url", "license_note")


def version_digest(data: dict, suite: dict | None, reviewer: str = "") -> str:
    raw = json.dumps({"data": data, "suite": suite, "reviewer": reviewer}, ensure_ascii=False,
                     sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def problem_data(problem: Problem) -> dict:
    return {key: getattr(problem, key) for key in FIELDS}


def snapshot(problem: Problem, version: ProblemVersion | None, *, with_suite=False):
    if version is None:
        if problem.revision:
            raise SandboxError("题目版本缺失")
        return problem
    if version.problem_id != problem.id or version_digest(version.data, version.suite, version.reviewer) != version.digest:
        raise SandboxError("题目版本完整性校验失败")
    fields = {**version.data, "id": problem.id, "revision": version.revision,
              "version_id": version.id, "is_active": problem.is_active}
    if with_suite:
        if version.suite is None:
            raise SandboxError("该版本测试快照不可用，请由管理员重新导入")
        fields["catalog_suite"] = JudgeSuite(**version.suite)
    return SimpleNamespace(**fields)


async def resolve_problem(session: AsyncSession, problem_id: int, version_id: int | None = None, *, with_suite=False):
    problem = await session.get(Problem, problem_id)
    if problem is None:
        raise SandboxError("题目不存在")
    version = (await session.get(ProblemVersion, version_id)) if version_id else None
    if version_id and version is None:
        raise SandboxError("题目版本缺失")
    if version is None and problem.revision:
        version = await session.scalar(select(ProblemVersion).where(
            ProblemVersion.problem_id == problem.id, ProblemVersion.revision == problem.revision))
    return snapshot(problem, version, with_suite=with_suite)


async def resolve_many(session: AsyncSession, pairs: list[tuple[Problem, int | None]]):
    ids = {vid for _, vid in pairs if vid}
    versions = {v.id: v for v in (await session.scalars(select(ProblemVersion).where(ProblemVersion.id.in_(ids)))).all()} if ids else {}
    if ids - versions.keys():
        raise SandboxError("题目版本缺失")
    # 无绑定的旧记录保持原语义；迁移会回填正式存量，临时导入/测试记录允许为空。
    cached = {}
    result = []
    for problem, vid in pairs:
        key = (problem.id, vid)
        if key not in cached:
            cached[key] = snapshot(problem, versions.get(vid)) if vid else problem
        result.append(cached[key])
    return result


async def capture_current(session: AsyncSession, problem: Problem) -> ProblemVersion:
    """调用方先持有 management_lock。revision=0 兼容未接管的旧脚本/测试记录。"""
    if problem.revision:
        row = await session.scalar(select(ProblemVersion).where(
            ProblemVersion.problem_id == problem.id, ProblemVersion.revision == problem.revision))
        if row is None or row.data != problem_data(problem):
            raise SandboxError("题目记录与发布版本不一致，请通过管理导入修复")
        snapshot(problem, row)
        return row
    data, suite = problem_data(problem), asdict(load_suite(problem))
    digest = version_digest(data, suite)
    row = await session.scalar(select(ProblemVersion).where(
        ProblemVersion.problem_id == problem.id, ProblemVersion.digest == digest).order_by(ProblemVersion.revision.desc()).limit(1))
    if row:
        return row
    revision = (await session.scalar(select(func.max(ProblemVersion.revision)).where(ProblemVersion.problem_id == problem.id)) or 0) + 1
    row = ProblemVersion(problem_id=problem.id, revision=revision, data=data, suite=suite, digest=digest)
    session.add(row)
    await session.flush()
    return row
