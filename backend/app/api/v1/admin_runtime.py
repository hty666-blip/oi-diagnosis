"""管理员的判题运维、模型检查和加密备份入口。"""
import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.deps import AdminUser, SessionDep
from app.api.input_types import ObjectPathId
from app.core.config import settings
from app.domain.models import (
    AdminAudit,
    AIConfiguration,
    AIConfigurationCheck,
    BackupArtifact,
    Diagnosis,
    JudgeJob,
    MaintenanceTask,
    Problem,
    Submission,
    User,
)
from app.services.admin_service import admin_write, audit
from app.services.backup_service import BackupError, BackupIO
from app.services.job_service import JobService, job_out
from app.services.operations_service import OperationsService, safe_failure_message
from app.services.problem_versions import resolve_many
from app.services.runtime_ai import (
    AICandidate,
    candidate_client,
    configuration,
    decrypt_key,
    observe_call,
    save_check,
    status,
)
from app.services.submission_service import build_runner
from app.services.worker_monitor import worker_status

router = APIRouter(prefix="/admin/runtime", tags=["admin-runtime"])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobAction(Input):
    status: Literal["queued", "running", "failed", "cancelled", "succeeded"]
    attempts: int = Field(strict=True, ge=0)
    manual_retries: int = Field(strict=True, ge=0)


class VersionInput(Input):
    expected_version: int = Field(strict=True, ge=0)


class ActivateInput(Input):
    check_id: UUID


class TaskInput(Input):
    request_id: UUID


def task_out(row):
    return {key: getattr(row, key) for key in ("id", "kind", "artifact_id", "status", "attempts", "error", "result", "created_at", "finished_at")}


def artifact_out(row):
    return {key: getattr(row, key) for key in ("id", "sha256", "size", "counts", "revision", "created_at", "verified_at")}


@router.get("/judging")
async def judging(session: SessionDep, user: AdminUser, state: str = "all", q: str = "", offset: int = 0):
    if state not in ("all", "queued", "running", "failed", "cancelled", "succeeded") or len(q) > 100 or not 0 <= offset <= 100000:
        raise HTTPException(422, "筛选条件超出范围")
    stmt = select(JudgeJob, Submission, Problem, User, Diagnosis).join(Submission, Submission.id == JudgeJob.submission_id)
    stmt = stmt.join(Problem, Problem.id == Submission.problem_id).outerjoin(User, User.id == Submission.student_id).outerjoin(Diagnosis, Diagnosis.submission_id == Submission.id)
    if state != "all":
        stmt = stmt.where(JudgeJob.status == state)
    if q.strip():
        stmt = stmt.where(Problem.pid.icontains(q.strip(), autoescape=True) | User.username.icontains(q.strip(), autoescape=True))
    rows = (await session.execute(stmt.order_by(JudgeJob.id.desc()).offset(offset).limit(51))).all()
    frozen = await resolve_many(session, [(p, s.problem_version_id) for _, s, p, _, _ in rows[:50]])
    items = []
    for (job, sub, _, student, diag), problem in zip(rows[:50], frozen, strict=True):
        protected = sub.stage == "READY" or bool(diag and diag.reviewer_id)
        public = job_out(job, can_cancel=not protected)
        public["last_error"] = safe_failure_message(job.last_error) if job.last_error else None
        public["retryable"] = public["retryable"] and not protected
        items.append({"job": public, "submission_id": sub.id, "stage": sub.stage,
                      "student": student.display_name or student.username if student else "旧记录",
                      "pid": problem.pid, "title": problem.title, "protected": protected,
                      "problem_version_id": sub.problem_version_id, "created_at": sub.created_at})
    check = await session.scalar(select(AdminAudit).where(AdminAudit.action == "judge.check").order_by(AdminAudit.id.desc()).limit(1))
    return {"items": items, "has_more": len(rows) > 50, "summary": await OperationsService(session, user).judging(),
            "workers": await worker_status(session), "backend": settings.judge_backend,
            "last_check": check.after if check else None}


@router.post("/judging/{submission_id}/{action}")
async def job_action(submission_id: ObjectPathId, action: Literal["retry", "cancel"], payload: JobAction,
                     request: Request, session: SessionDep, user: AdminUser):
    await admin_write(session, user, request)
    row = await session.scalar(select(JudgeJob).where(JudgeJob.submission_id == submission_id).with_for_update())
    if row is None or (row.status, row.attempts, row.manual_retries) != (payload.status, payload.attempts, payload.manual_retries):
        raise HTTPException(409, "任务状态已变化，请刷新后核对")
    before = {"status": row.status, "attempts": row.attempts, "manual_retries": row.manual_retries}
    row = await getattr(JobService(session), action)(submission_id, user, commit=False)
    audit(session, user, "judge." + action, f"submission:{submission_id}", before,
          {"status": row.status, "attempts": row.attempts, "manual_retries": row.manual_retries})
    await session.commit()
    result = job_out(row, can_cancel=True)
    result["last_error"] = safe_failure_message(row.last_error) if row.last_error else None
    return result


@router.post("/judge-check")
async def judge_check(request: Request, session: SessionDep, user: AdminUser, language: Literal["cpp17", "c11", "java17", "python3"] = "cpp17"):
    await admin_write(session, user, request)
    await session.commit()
    code = {"cpp17": '#include <iostream>\nint main(){std::cout<<"OK\\n";}',
            "c11": '#include <stdio.h>\nint main(void){puts("OK");return 0;}',
            "java17": 'public class Main { public static void main(String[] a){System.out.println("OK");} }',
            "python3": 'print("OK")'}[language]
    runner, artifact_id, ok = None, None, False
    try:
        async with asyncio.timeout(25):
            runner = build_runner()
            runner.set_language(language)
            compiled = await runner.compile(code)
            artifact_id = compiled.artifact_id
            if compiled.ok:
                result = await runner.run(artifact_id, stdin="", time_limit_ms=2000, memory_limit_mb=256)
                ok = result.status == "Accepted" and result.exit_code == 0 and result.stdout.strip() == "OK"
    except Exception:
        ok = False
    finally:
        if artifact_id and hasattr(runner, "cleanup"):
            with suppress(Exception):
                await runner.cleanup(artifact_id)
        if hasattr(runner, "close"):
            with suppress(Exception):
                await runner.close()
    await admin_write(session, user, request)
    result = {"ok": ok, "language": language, "backend": settings.judge_backend, "checked_at": datetime.now(UTC).isoformat()}
    audit(session, user, "judge.check", "judge", {}, result)
    await session.commit()
    return result


@router.get("/ai")
async def ai_status(session: SessionDep, user: AdminUser):
    return await status(session)


@router.post("/ai/check")
async def check_ai(payload: AICandidate, request: Request, session: SessionDep, user: AdminUser):
    await admin_write(session, user, request)
    client, key = await candidate_client(session, payload)
    await session.commit()
    ok, _ = await client.ping()
    await observe_call(session, client, "check", used=ok)
    await admin_write(session, user, request)
    current = await configuration(session)
    if current["version"] != payload.expected_version:
        raise HTTPException(409, "检查期间配置已变化，原配置保留，请重新检查")
    check = await save_check(session, user, payload, client, key, ok)
    audit(session, user, "ai.check", "ai", {}, {"ok": ok, "base_url": payload.base_url, "model": payload.model})
    await session.commit()
    return {"check_id": check.id, "ok": ok, "error": check.error, "latency_ms": check.latency_ms, "expires_at": check.expires_at}


@router.post("/ai/activate")
async def activate_ai(payload: ActivateInput, request: Request, session: SessionDep, user: AdminUser):
    await admin_write(session, user, request)
    check = await session.get(AIConfigurationCheck, str(payload.check_id))
    current = await configuration(session)
    if (not settings.llm_enabled or check is None or check.actor_id != user.id or not check.ok
        or check.expires_at <= datetime.now(UTC) or check.base_version != current["version"]):
        raise HTTPException(409, "检查已失效或未通过，请重新检查后启用")
    try:
        decrypt_key(check.encrypted_key)
    except Exception as exc:
        raise HTTPException(409, "检查凭据已不可用，请重新填写密钥检查") from exc
    row = await session.get(AIConfiguration, 1)
    if row is None:
        row = AIConfiguration(id=1)
        session.add(row)
    row.version, row.enabled = current["version"] + 1, True
    row.base_url, row.model, row.encrypted_key, row.checked_at = check.base_url, check.model, check.encrypted_key, check.created_at
    audit(session, user, "ai.activate", "ai", {"version": current["version"]}, {"version": row.version, "model": row.model, "base_url": row.base_url})
    await session.commit()
    return await status(session)


@router.post("/ai/disable")
async def disable_ai(payload: VersionInput, request: Request, session: SessionDep, user: AdminUser):
    await admin_write(session, user, request)
    current = await configuration(session)
    if payload.expected_version != current["version"]:
        raise HTTPException(409, "配置已变化，请刷新后重试")
    row = await session.get(AIConfiguration, 1)
    if row is None:
        from app.services.runtime_ai import encrypt_key
        row = AIConfiguration(id=1, base_url=current["base_url"], model=current["model"], encrypted_key=encrypt_key(current["key"]))
        session.add(row)
    row.enabled, row.version = False, current["version"] + 1
    audit(session, user, "ai.disable", "ai", {"version": current["version"]}, {"version": row.version, "enabled": False})
    await session.commit()
    return await status(session)


@router.get("/backups")
async def backups(session: SessionDep, user: AdminUser):
    artifacts = (await session.scalars(select(BackupArtifact).order_by(BackupArtifact.created_at.desc()).limit(30))).all()
    tasks = (await session.scalars(select(MaintenanceTask).order_by(MaintenanceTask.created_at.desc()).limit(50))).all()
    configured = False
    try:
        BackupIO().verify_url(settings.backup_verify_database_url)
        configured = True
    except (BackupError, ValueError):
        pass
    return {"items": [artifact_out(a) for a in artifacts], "tasks": [task_out(t) for t in tasks], "verify_configured": configured,
            "workers": await worker_status(session)}


@router.post("/backups/{action}", status_code=202)
async def enqueue_backup(action: Literal["create", "verify"], payload: TaskInput, request: Request,
                         session: SessionDep, user: AdminUser, artifact_id: UUID | None = None):
    await admin_write(session, user, request)
    old = await session.scalar(select(MaintenanceTask).where(MaintenanceTask.request_id == str(payload.request_id)))
    if old:
        if old.actor_id != user.id or old.kind != ("backup" if action == "create" else "verify") or (artifact_id and old.artifact_id != str(artifact_id)):
            raise HTTPException(409, "请求编号已用于其他操作")
        return task_out(old)
    if action == "create":
        artifact = BackupArtifact(id=str(uuid4()), created_by=user.id)
        session.add(artifact)
        await session.flush()
    else:
        artifact = await session.get(BackupArtifact, str(artifact_id)) if artifact_id else None
        if artifact is None or not artifact.file_name:
            raise HTTPException(409, "备份尚未生成或不存在")
        try:
            BackupIO().verify_url(settings.backup_verify_database_url)
        except (BackupError, ValueError) as exc:
            raise HTTPException(409, "尚未配置安全的独立恢复验证库") from exc
    task = MaintenanceTask(id=str(uuid4()), request_id=str(payload.request_id), kind="backup" if action == "create" else "verify",
                           artifact_id=artifact.id, actor_id=user.id, actor_version=user.token_version)
    session.add(task)
    await session.flush()
    audit(session, user, "backup." + action, f"backup:{artifact.id}", {}, {"task_id": task.id})
    await session.commit()
    return task_out(task)


@router.post("/maintenance/{task_id}/retry", status_code=202)
async def retry_maintenance(task_id: UUID, request: Request, session: SessionDep, user: AdminUser):
    await admin_write(session, user, request)
    task = await session.get(MaintenanceTask, str(task_id), with_for_update=True)
    if task is None or task.status != "failed" or task.attempts >= 3:
        raise HTTPException(409, "任务不可重试，请核对错误并重新发起操作")
    task.status, task.owner, task.lease_until, task.error = "queued", None, None, ""
    task.actor_id, task.actor_version, task.finished_at = user.id, user.token_version, None
    audit(session, user, "backup.retry", f"task:{task.id}", {}, {"attempts": task.attempts})
    await session.commit()
    return task_out(task)


@router.get("/backups/{artifact_id}/download")
async def download_backup(artifact_id: UUID, session: SessionDep, user: AdminUser):
    artifact = await session.get(BackupArtifact, str(artifact_id))
    if artifact is None or not artifact.file_name:
        raise HTTPException(404, "备份不可用")
    try:
        path = BackupIO().path(artifact.file_name)
        if not path.is_file():
            raise BackupError("archive_missing")
    except BackupError as exc:
        raise HTTPException(404, "备份文件不可用") from exc
    return FileResponse(path, filename=f"oi-backup-{artifact.id}.oibak", media_type="application/octet-stream", headers={"Cache-Control": "no-store"})
