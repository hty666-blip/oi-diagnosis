"""题库管理：服务器保存预览，确认时复核版本并原子安装。"""
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import delete, select

from app.api.deps import AdminUser, SessionDep
from app.api.input_types import ObjectPathId
from app.capabilities.sandbox import SandboxError
from app.capabilities.test_suite import PACKAGE_ROOT
from app.domain.models import Problem, ProblemImportDraft, ProblemVersion
from app.services.admin_service import admin_write, audit
from app.services.problem_catalog import ProblemInput, adopt_legacy, install_version, local_package
from app.services.problem_versions import snapshot

router = APIRouter(prefix="/admin/problems", tags=["admin-problems"])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImportInput(Input):
    problems: list[ProblemInput] = Field(min_length=1, max_length=20)
    rights_confirmed: StrictBool


class LocalInput(Input):
    pids: list[Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]] = Field(min_length=1, max_length=20)
    rights_confirmed: StrictBool


class ConfirmInput(Input):
    preview_id: UUID


class StateInput(Input):
    expected_version: int = Field(strict=True, ge=0)
    is_active: StrictBool


def summary(problem: Problem, version: ProblemVersion | None):
    suite = version.suite if version else None
    notes = []
    try:
        snapshot(problem, version)
    except SandboxError:
        notes.append("版本完整性校验未通过")
    if version and (suite is None or not suite.get("cases")):
        notes.append("没有可执行的测试快照")
    if not problem.source.strip() or not problem.license_note.strip():
        notes.append("来源或许可说明待补齐")
    return {"id": problem.id, "pid": problem.pid, "title": problem.title, "revision": problem.revision,
            "catalog_version": problem.catalog_version, "is_active": problem.is_active,
            "package_level": problem.package_level, "source": problem.source, "source_url": problem.source_url,
            "license_note": problem.license_note, "samples": len(problem.samples or []),
            "cases": len(suite.get("cases", [])) if suite else None, "scope": suite.get("scope") if suite else None,
            "notes": notes}


@router.get("")
async def list_problems(session: SessionDep, user: AdminUser, q: str = "", offset: int = 0,
                        state: Literal["all", "active", "inactive"] = "all"):
    if len(q) > 120 or not 0 <= offset <= 100000:
        raise HTTPException(422, "查询条件超出范围")
    stmt = select(Problem, ProblemVersion).outerjoin(ProblemVersion,
        (ProblemVersion.problem_id == Problem.id) & (ProblemVersion.revision == Problem.revision))
    if q.strip():
        stmt = stmt.where(Problem.pid.icontains(q.strip(), autoescape=True) | Problem.title.icontains(q.strip(), autoescape=True))
    if state != "all":
        stmt = stmt.where(Problem.is_active.is_(state == "active"))
    rows = (await session.execute(stmt.order_by(Problem.id).offset(offset).limit(51))).all()
    return {"items": [summary(p, v) for p, v in rows[:50]], "has_more": len(rows) > 50}


@router.get("/local-packages")
async def local_packages(user: AdminUser):
    rows = []
    try:
        paths = sorted(PACKAGE_ROOT.iterdir())
    except OSError:
        return rows
    for path in paths:
        if not path.is_dir():
            continue
        try:
            value = local_package(path.name)
            rows.append({"pid": value.pid, "title": value.title, "valid": True,
                         "cases": len(value.cases), "package_level": value.package_level})
        except (OSError, ValueError, SandboxError):
            rows.append({"pid": path.name, "title": "题包校验未通过", "valid": False, "cases": 0, "package_level": ""})
    return rows


async def save_preview(session, user, payload: ImportInput):
    if not payload.rights_confirmed:
        raise HTTPException(422, "请先确认有权使用和公开这些题面与评测数据")
    if len({p.pid for p in payload.problems}) != len(payload.problems):
        raise HTTPException(422, "同批题号不能重复")
    if len(payload.model_dump_json().encode("utf-8")) > 8_000_000:
        raise HTTPException(422, "每批题包内容不得超过 8 MB")
    records, rows = [], []
    for item in payload.problems:
        record = item.record()
        existing = await session.scalar(select(Problem).where(Problem.pid == item.pid))
        current = await session.scalar(select(ProblemVersion).where(
            ProblemVersion.problem_id == existing.id, ProblemVersion.revision == existing.revision)) if existing else None
        unchanged = current is not None and current.digest == record["digest"]
        record["expected"] = {"id": existing.id, "catalog_version": existing.catalog_version} if existing else None
        records.append(record)
        rows.append({"pid": item.pid, "title": item.title, "existing_revision": existing.revision if existing else None,
                     "action": "unchanged" if unchanged else "new_version" if existing else "create",
                     "package_level": item.package_level, "scope": record["suite"]["scope"],
                     "cases": len(record["suite"]["cases"]), "samples": len(item.samples),
                     "source": item.source, "source_url": item.source_url, "license_note": item.license_note,
                     "reviewer": item.reviewer, "digest": record["digest"],
                     "notice": "相同内容不追加版本，保留当前上下架状态" if unchanged else "新版本先下架；旧训练和提交保留原版本"})
    now = datetime.now(UTC)
    await session.execute(delete(ProblemImportDraft).where(ProblemImportDraft.expires_at < now))
    draft = ProblemImportDraft(id=str(uuid4()), actor_id=user.id, records=records, expires_at=now + timedelta(minutes=30))
    session.add(draft)
    await session.commit()
    return {"preview_id": draft.id, "expires_at": draft.expires_at, "rows": rows,
            "notice": "仅校验结构、来源说明与测试数量；答案正确性仍需审核人核对。确认后新版本待上架。"}


@router.post("/import/preview")
async def preview_import(payload: ImportInput, request: Request, session: SessionDep, user: AdminUser):
    await admin_write(session, user, request)
    return await save_preview(session, user, payload)


@router.post("/local-packages/preview")
async def preview_local(payload: LocalInput, request: Request, session: SessionDep, user: AdminUser):
    await admin_write(session, user, request)
    try:
        problems = [local_package(pid) for pid in payload.pids]
    except (OSError, ValueError, SandboxError) as exc:
        raise HTTPException(422, "本地题包校验失败，请检查元数据、样例、测试配对和资源限制") from exc
    return await save_preview(session, user, ImportInput(problems=problems, rights_confirmed=payload.rights_confirmed))


@router.post("/import/confirm")
async def confirm_import(payload: ConfirmInput, request: Request, session: SessionDep, user: AdminUser):
    await admin_write(session, user, request)
    draft = await session.get(ProblemImportDraft, str(payload.preview_id))
    if draft is None or draft.actor_id != user.id:
        raise HTTPException(404, "导入预览不存在或不属于当前账号")
    if draft.expires_at <= datetime.now(UTC):
        raise HTTPException(409, "预览已过期，请重新校验")
    if draft.result is not None:
        return {"items": draft.result, "replayed": True}
    for record in draft.records:
        row = await session.scalar(select(Problem).where(Problem.pid == record["data"]["pid"]))
        expected = record["expected"]
        if ((expected is None and row is not None) or
            (expected is not None and (row is None or row.id != expected["id"] or row.catalog_version != expected["catalog_version"]))):
            raise HTTPException(409, "题目版本或上下架状态已变化，请重新预览；本批尚未导入")
    result = []
    for record in draft.records:
        row, version, changed = await install_version(session, record, user.id)
        result.append({"id": row.id, "pid": row.pid, "revision": version.revision, "changed": changed, "is_active": row.is_active})
        if changed:
            audit(session, user, "problem.import", f"problem:{row.id}", record["expected"] or {},
                  {"pid": row.pid, "revision": version.revision, "digest": version.digest, "is_active": False})
    draft.result = result
    await session.commit()
    return {"items": result, "replayed": False}


@router.post("/{problem_id}/state")
async def set_state(problem_id: ObjectPathId, payload: StateInput, request: Request, session: SessionDep, user: AdminUser):
    await admin_write(session, user, request)
    row = await session.get(Problem, problem_id)
    if row is None:
        raise HTTPException(404, "题目不存在")
    if row.catalog_version != payload.expected_version:
        raise HTTPException(409, "题目已变化，请刷新后重试")
    await adopt_legacy(session, row)
    version = await session.scalar(select(ProblemVersion).where(ProblemVersion.problem_id == row.id, ProblemVersion.revision == row.revision))
    if payload.is_active:
        snapshot(row, version)
    if payload.is_active and (version is None or not version.suite or not version.suite.get("cases")
        or not row.source.strip() or not row.license_note.strip()
        or (row.package_level in ("A", "B") and (version.suite.get("scope") != "package" or not version.reviewer.strip()))):
        raise HTTPException(409, "上架前需有效测试快照、来源许可及正式测试审核人；请补齐后重新导入")
    before = {"is_active": row.is_active, "revision": row.revision}
    row.is_active = payload.is_active
    row.catalog_version += 1
    audit(session, user, "problem.state", f"problem:{row.id}", before, {"is_active": row.is_active, "revision": row.revision})
    await session.commit()
    return summary(row, version)


@router.get("/{problem_id}/versions")
async def history(problem_id: ObjectPathId, session: SessionDep, user: AdminUser):
    if await session.get(Problem, problem_id) is None:
        raise HTTPException(404, "题目不存在")
    rows = (await session.scalars(select(ProblemVersion).where(ProblemVersion.problem_id == problem_id)
                                 .order_by(ProblemVersion.revision.desc()).limit(31))).all()
    return {"items": [{"id": v.id, "revision": v.revision, "title": v.data["title"], "source": v.data["source"],
                       "digest": v.digest, "reviewer": v.reviewer, "created_by": v.created_by, "created_at": v.created_at,
                       "cases": len(v.suite.get("cases", [])) if v.suite else None} for v in rows[:30]], "truncated": len(rows) > 30}


@router.get("/{problem_id}/versions/{version_id}")
async def version_content(problem_id: ObjectPathId, version_id: ObjectPathId, session: SessionDep, user: AdminUser):
    version = await session.get(ProblemVersion, version_id)
    if version is None or version.problem_id != problem_id:
        raise HTTPException(404, "题目版本不存在")
    data = {key: value for key, value in version.data.items() if key != "has_testdata"}
    data["samples"] = [{"input": c["input"], "output": c["output"]} for c in data.get("samples", [])]
    data.update(reviewer=version.reviewer, time_limit_ms=(version.suite or {}).get("time_limit_ms", 2000),
                memory_limit_mb=(version.suite or {}).get("memory_limit_mb", 256),
                cases=(version.suite or {}).get("cases", []) if data["package_level"] in ("A", "B") else [])
    return {"problem": data, "revision": version.revision, "snapshot_available": version.suite is not None}
