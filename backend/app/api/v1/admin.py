"""平台管理基础：账号、班级、明确转交和审计。"""
from asyncio import to_thread
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.api.deps import AdminUser, SessionDep
from app.api.input_types import ObjectId, ObjectPathId
from app.core.security import hash_password
from app.core.text_validation import StorageText
from app.domain.models import AdminAudit, ClassTeacher, SchoolClass, Training, TrainingMember, User
from app.services.admin_service import (
    admin_write,
    audit,
    get_class,
    get_user,
    protect_last_admin,
    user_out,
)
from app.services.operations_service import OperationsService
from app.services.runtime_ai import configuration
from app.services.worker_monitor import worker_status

router = APIRouter(prefix="/admin", tags=["admin"])
Role = Literal["student", "teacher", "admin"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountCreate(Input):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    display_name: StorageText = Field(min_length=1, max_length=64)
    role: Role = "student"
    password: StorageText = Field(min_length=8, max_length=128)
    class_id: ObjectId | None = None

    @field_validator("username", mode="before")
    @classmethod
    def normalize(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("display_name")
    @classmethod
    def name_not_blank(cls, value: str):
        if not value.strip():
            raise ValueError("姓名不能为空")
        return value.strip()


class AccountEdit(Input):
    expected_version: int = Field(ge=0)
    display_name: StorageText = Field(min_length=1, max_length=64)
    role: Role
    enabled: bool

    _name = field_validator("display_name")(AccountCreate.name_not_blank.__func__)


class PasswordReset(Input):
    expected_version: int = Field(ge=0)
    password: StorageText = Field(min_length=8, max_length=128)


class Transfer(Input):
    expected_version: int = Field(ge=0)
    class_id: ObjectId | None


class ClassInput(Input):
    name: StorageText = Field(min_length=1, max_length=64)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str):
        if not value.strip():
            raise ValueError("班级名称不能为空")
        return value.strip()


class ClassEdit(ClassInput):
    expected_version: int = Field(ge=0)
    archived: bool
    teacher_ids: list[ObjectId] = Field(max_length=30)
    transfer_to: ObjectId | None = None


class AccountBatch(Input):
    rows: list[AccountCreate] = Field(min_length=1, max_length=100)


class ClassCreate(ClassInput):
    teacher_ids: list[ObjectId] = Field(default_factory=list, max_length=30)
    student_accounts: list[AccountCreate] = Field(default_factory=list, max_length=100)


@router.get("/overview")
async def overview(session: SessionDep, user: AdminUser):
    workers = await worker_status(session)
    ai = await configuration(session)
    classes = (await session.scalars(select(SchoolClass).where(SchoolClass.archived.is_(False)))).all()
    assigned = set((await session.scalars(select(ClassTeacher.class_id).join(User, User.id == ClassTeacher.teacher_id)
                                        .where(User.enabled.is_(True), User.role == "teacher"))).all())
    return {"observed_at": datetime.now(UTC), "database": "connected",
            "accounts": {role: await session.scalar(select(func.count()).select_from(User).where(User.role == role)) for role in ("student", "teacher", "admin")},
            "disabled": await session.scalar(select(func.count()).select_from(User).where(User.enabled.is_(False))),
            "unassigned_students": await session.scalar(select(func.count()).select_from(User).where(User.role == "student", User.enabled.is_(True), User.class_id.is_(None))),
            "classes_without_teacher": [{"id": c.id, "name": c.name} for c in classes if c.id not in assigned],
            "active_classes": len(classes), "llm_enabled": ai["enabled"],
            "worker_status": "online" if any(w["online"] and w["kind"] == "judge" for w in workers["items"]) else "unknown",
            "judging": await OperationsService(session, user).judging()}


@router.get("/users")
async def users(session: SessionDep, user: AdminUser, q: str = "", offset: int = 0, group_id: int | None = None):
    if not 0 <= offset <= 100000 or len(q) > 64 or (group_id is not None and not 0 <= group_id <= 2**63 - 1):
        raise HTTPException(422, "查询条件超出范围")
    stmt = select(User)
    if group_id is not None:
        stmt = stmt.where(User.role == "student", User.class_id == group_id if group_id else User.class_id.is_(None))
    if q.strip():
        term = q.strip().replace("!", "!!").replace("%", "!%").replace("_", "!_")
        stmt = stmt.where(User.username.ilike(f"%{term}%", escape="!") | User.display_name.ilike(f"%{term}%", escape="!"))
    rows = (await session.scalars(stmt.order_by(User.id).offset(offset).limit(51))).all()
    return {"items": [user_out(r) for r in rows[:50]], "has_more": len(rows) > 50}


async def validate_accounts(session, payload: AccountBatch):
    seen, errors = set(), []
    existing = set((await session.scalars(select(User.username).where(User.username.in_([r.username for r in payload.rows])))).all())
    for i, row in enumerate(payload.rows, 1):
        if row.username in seen or row.username in existing:
            errors.append({"row": i, "message": "用户名重复或已存在"})
        seen.add(row.username)
        if row.class_id is not None:
            group = await session.get(SchoolClass, row.class_id)
            if row.role != "student" or group is None or group.archived:
                errors.append({"row": i, "message": "只有学生可分班，且班级必须存在并未归档"})
    return errors


async def insert_accounts(session, actor, payload: AccountBatch, *, single=False):
    errors = await validate_accounts(session, payload)
    if errors:
        if single:
            raise HTTPException(409, errors[0]["message"])
        raise HTTPException(409, {"message": "导入未执行，请修正后重新预览", "errors": errors})
    result = []
    for item in payload.rows:
        group = await get_class(session, item.class_id)
        row = User(username=item.username, display_name=item.display_name, role=item.role,
                   password_hash=await to_thread(hash_password, item.password), class_id=item.class_id,
                   class_name=group.name if group else "", must_change_password=True)
        session.add(row)
        await session.flush()
        audit(session, actor, "account.create", f"user:{row.id}", {}, user_out(row))
        result.append(user_out(row))
    return result


@router.post("/users/import/preview")
async def import_preview(payload: AccountBatch, session: SessionDep, user: AdminUser):
    return {"errors": await validate_accounts(session, payload), "rows": [
        {"row": i, "username": r.username, "display_name": r.display_name, "role": r.role, "class_id": r.class_id}
        for i, r in enumerate(payload.rows, 1)]}


@router.post("/users/import")
async def import_accounts(payload: AccountBatch, request: Request, session: SessionDep, user: AdminUser):
    await admin_write(session, user, request)
    result = await insert_accounts(session, user, payload)
    await session.commit()
    return result


@router.post("/users")
async def create_account(payload: AccountCreate, request: Request, session: SessionDep, user: AdminUser):
    await admin_write(session, user, request)
    result = await insert_accounts(session, user, AccountBatch(rows=[payload]), single=True)
    await session.commit()
    return result[0]


@router.patch("/users/{user_id}")
async def edit_account(user_id: ObjectPathId, payload: AccountEdit, request: Request, session: SessionDep, user: AdminUser):
    await admin_write(session, user, request)
    row = await get_user(session, user_id, payload.expected_version)
    await protect_last_admin(session, row, payload.role, payload.enabled)
    if row.role != payload.role and (row.class_id is not None or await session.scalar(select(ClassTeacher.teacher_id).where(ClassTeacher.teacher_id == row.id).limit(1)) or await session.scalar(select(Training.id).where(Training.teacher_id == row.id).limit(1))):
        raise HTTPException(409, "请先移出班级、解除负责关系并转交训练，再更改角色")
    before = user_out(row)
    row.display_name, row.role, row.enabled = payload.display_name, payload.role, payload.enabled
    row.token_version += 1
    audit(session, user, "account.update", f"user:{row.id}", before, user_out(row))
    await session.commit()
    return user_out(row)


@router.post("/users/{user_id}/password")
async def reset_password(user_id: ObjectPathId, payload: PasswordReset, request: Request, session: SessionDep, user: AdminUser):
    await admin_write(session, user, request)
    row = await get_user(session, user_id, payload.expected_version)
    before = user_out(row)
    row.password_hash = await to_thread(hash_password, payload.password)
    row.token_version += 1
    row.must_change_password = True
    audit(session, user, "account.reset_password", f"user:{row.id}", before, user_out(row))
    await session.commit()
    return user_out(row)


async def transfer_impact(session, user_id, payload):
    row = await get_user(session, user_id, payload.expected_version)
    if row.role != "student":
        raise HTTPException(422, "转班仅适用于学生")
    target = await get_class(session, payload.class_id)
    old_tasks = await session.scalar(select(func.count()).select_from(TrainingMember).join(Training, Training.id == TrainingMember.training_id)
        .where(TrainingMember.student_id == row.id, Training.class_id == row.class_id, Training.status == "published"))
    return row, target, {"student": row.display_name, "from": row.class_name, "to": target.name if target else "未分班",
                         "affected_trainings": old_tasks if row.class_id != payload.class_id else 0,
                         "notice": "转班后旧班训练和反馈将不再可见，历史数据及已发布名单保留；新班已发布训练不会自动加入。"}


@router.post("/users/{user_id}/transfer/preview")
async def preview_transfer(user_id: ObjectPathId, payload: Transfer, session: SessionDep, user: AdminUser):
    return (await transfer_impact(session, user_id, payload))[2]


@router.post("/users/{user_id}/transfer")
async def transfer_student(user_id: ObjectPathId, payload: Transfer, request: Request, session: SessionDep, user: AdminUser):
    await admin_write(session, user, request)
    row, group, impact = await transfer_impact(session, user_id, payload)
    before = user_out(row)
    row.class_id, row.class_name = payload.class_id, group.name if group else ""
    row.token_version += 1
    audit(session, user, "student.transfer", f"user:{row.id}", before, {**user_out(row), "impact": impact})
    await session.commit()
    return user_out(row)


@router.get("/classes")
async def classes(session: SessionDep, user: AdminUser):
    rows = (await session.scalars(select(SchoolClass).order_by(SchoolClass.id))).all()
    pairs = (await session.execute(select(ClassTeacher.class_id, ClassTeacher.teacher_id))).all()
    counts = dict((await session.execute(select(User.class_id, func.count()).where(User.role == "student").group_by(User.class_id))).all())
    owners = (await session.execute(select(Training.class_id, Training.teacher_id, func.count()).group_by(Training.class_id, Training.teacher_id))).all()
    return [{"id": r.id, "name": r.name, "archived": r.archived, "version": r.version,
             "teacher_ids": [tid for cid, tid in pairs if cid == r.id], "students": counts.get(r.id, 0),
             "training_owners": [{"teacher_id": tid, "count": n} for cid, tid, n in owners if cid == r.id]} for r in rows]


@router.get("/teachers")
async def teachers(session: SessionDep, user: AdminUser):
    rows = (await session.scalars(select(User).where(User.role == "teacher").order_by(User.id))).all()
    return [{"id": r.id, "name": r.display_name or r.username, "enabled": r.enabled} for r in rows]


@router.post("/classes")
async def create_class(payload: ClassCreate, request: Request, session: SessionDep, user: AdminUser):
    await admin_write(session, user, request)
    teacher_ids = set(payload.teacher_ids)
    valid = set((await session.scalars(select(User.id).where(
        User.id.in_(teacher_ids), User.role == "teacher", User.enabled.is_(True)
    ))).all())
    if len(teacher_ids) != len(payload.teacher_ids) or teacher_ids != valid:
        raise HTTPException(422, "教练重复、不存在或已停用")
    if any(r.role != "student" or r.class_id is not None for r in payload.student_accounts):
        raise HTTPException(422, "新班名单只允许未指定班级的新学生账号")
    if payload.student_accounts:
        errors = await validate_accounts(session, AccountBatch(rows=payload.student_accounts))
        if errors:
            raise HTTPException(409, {"message": "班级未创建，请修正学生名单后重试", "errors": errors})
    row = SchoolClass(name=payload.name)
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, "班级名称已存在") from exc
    session.add_all([ClassTeacher(class_id=row.id, teacher_id=tid) for tid in teacher_ids])
    if payload.student_accounts:
        await insert_accounts(session, user, AccountBatch(rows=[
            item.model_copy(update={"class_id": row.id}) for item in payload.student_accounts
        ]))
    audit(session, user, "class.create", f"class:{row.id}", {},
          {"name": row.name, "teacher_ids": sorted(teacher_ids), "students_created": len(payload.student_accounts)})
    await session.commit()
    return {"id": row.id}


@router.patch("/classes/{class_id}")
async def edit_class(class_id: ObjectPathId, payload: ClassEdit, request: Request, session: SessionDep, user: AdminUser):
    await admin_write(session, user, request)
    row = await get_class(session, class_id, active=False)
    if row.version != payload.expected_version:
        raise HTTPException(409, "班级已被修改，请刷新后重试")
    teacher_ids = set(payload.teacher_ids)
    valid = set((await session.scalars(select(User.id).where(User.id.in_(teacher_ids), User.role == "teacher", User.enabled.is_(True)))).all())
    if len(teacher_ids) != len(payload.teacher_ids) or teacher_ids != valid:
        raise HTTPException(422, "教练重复、不存在或已停用")
    if payload.transfer_to is not None and payload.transfer_to not in teacher_ids:
        raise HTTPException(422, "接收训练的教练必须是本班负责教练")
    old_ids = list((await session.scalars(select(ClassTeacher.teacher_id).where(ClassTeacher.class_id == class_id))).all())
    before = {"name": row.name, "archived": row.archived, "teacher_ids": old_ids}
    duplicate = await session.scalar(select(SchoolClass.id).where(SchoolClass.name == payload.name, SchoolClass.id != row.id))
    if duplicate:
        raise HTTPException(409, "班级名称已存在")
    # 先锁训练，再修改关系；已发布名单与原始创建人永不改写。
    tasks = (await session.scalars(select(Training).where(Training.class_id == class_id).order_by(Training.id).with_for_update())).all()
    transfers = []
    for task in tasks:
        if payload.transfer_to is not None and task.teacher_id not in teacher_ids:
            task.original_teacher_id = task.original_teacher_id or task.teacher_id
            transfers.append({"training_id": task.id, "from": task.teacher_id, "to": payload.transfer_to})
            task.teacher_id = payload.transfer_to
        task.class_name = payload.name
    await session.execute(update(User).where(User.class_id == class_id).values(class_name=payload.name))
    await session.execute(update(User).where(User.role == "teacher", User.id.in_(set(old_ids) | teacher_ids)).values(class_id=None, class_name=""))
    await session.execute(delete(ClassTeacher).where(ClassTeacher.class_id == class_id))
    session.add_all([ClassTeacher(class_id=class_id, teacher_id=tid) for tid in teacher_ids])
    row.name, row.archived = payload.name, payload.archived
    row.version += 1
    audit(session, user, "class.update", f"class:{row.id}", before,
          {"name": row.name, "archived": row.archived, "teacher_ids": sorted(teacher_ids), "transfers": transfers})
    await session.commit()
    return {"id": row.id, "version": row.version, "transferred": len(transfers)}


@router.get("/audit")
async def audit_history(session: SessionDep, user: AdminUser, before_id: int | None = None):
    stmt = select(AdminAudit, User.display_name, User.username).join(User, User.id == AdminAudit.actor_id)
    if before_id is not None:
        stmt = stmt.where(AdminAudit.id < before_id)
    rows = (await session.execute(stmt.order_by(AdminAudit.id.desc()).limit(51))).all()
    return {"items": [{"id": r.id, "actor": name or username, "action": r.action, "target": r.target,
                       "before": r.before, "after": r.after, "created_at": r.created_at} for r, name, username in rows[:50]],
            "has_more": len(rows) > 50}
