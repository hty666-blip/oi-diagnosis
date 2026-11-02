"""训练草稿、发布及授权完成矩阵。"""
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AwareDatetime, BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, TeacherUser
from app.api.input_types import ObjectId, ObjectPathId
from app.domain.models import Problem, SchoolClass, Training, TrainingMember, TrainingProblem, User
from app.infra.db import get_session
from app.services.admin_service import training_write
from app.services.class_access import (
    active_class,
    same_class,
    student_class_scope,
    teacher_class_scope,
)
from app.services.student_followup_service import (
    FeedbackCreate,
    create_feedback,
    feedback_history,
    mark_feedback_read,
)
from app.services.training_analysis import analyze_training
from app.services.training_lesson_service import LessonExpected, LessonSave, TrainingLessonService
from app.services.training_service import accessible_training, training_detail, training_out

router = APIRouter(prefix="/trainings", tags=["trainings"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class TrainingProblemInput(BaseModel):
    pid: str = Field(min_length=1, max_length=32)
    required: bool = True


class TrainingInput(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    class_name: str = Field(min_length=1, max_length=64)
    objective: str = Field(default="", max_length=500)
    description: str = Field(default="", max_length=5000)
    due_at: AwareDatetime
    problems: list[TrainingProblemInput] = Field(min_length=1, max_length=20)
    student_ids: list[ObjectId] | None = Field(default=None, min_length=1, max_length=200)
    source_training_id: ObjectId | None = None

    @field_validator("title", "class_name", "objective", "description")
    @classmethod
    def valid_text(cls, value: str):
        if "\x00" in value:
            raise ValueError("内容不能含空字符")
        value.encode("utf-8")
        return value.strip()


@router.get("/roster")
async def roster(session: SessionDep, user: TeacherUser):
    stmt = select(User).where(User.role == "student", User.class_name != "", User.enabled.is_(True))
    if user.role != "admin":
        stmt = stmt.where(teacher_class_scope(user))
    students = (await session.scalars(stmt.order_by(User.class_name, User.id))).all()
    return [{"id": s.id, "name": s.display_name or s.username, "class_name": s.class_name} for s in students]


@router.get("")
async def list_trainings(session: SessionDep, user: CurrentUser):
    stmt = select(Training).order_by(Training.id.desc())
    if user.role == "student":
        stmt = stmt.join(TrainingMember, TrainingMember.training_id == Training.id).where(
            TrainingMember.student_id == user.id, Training.status == "published", student_class_scope(user))
    elif user.role == "teacher":
        stmt = stmt.where(Training.teacher_id == user.id, teacher_class_scope(user, Training))
    return [training_out(row, include_audience=user.role != "student") for row in (await session.scalars(stmt.limit(100))).all()]


async def save_draft(session: AsyncSession, user: User, payload: TrainingInput, row: Training | None):
    if not payload.title or not payload.class_name:
        raise HTTPException(422, "请填写训练名称和班级")
    group = await session.scalar(select(SchoolClass).where(SchoolClass.name == payload.class_name))
    if group and group.archived:
        raise HTTPException(409, "班级已归档，请恢复后布置训练")
    target = User(class_id=group.id if group else None, class_name=payload.class_name)
    if not await same_class(session, user, target):
        raise HTTPException(403, "只能给本班布置训练")
    if payload.due_at <= datetime.now(UTC):
        raise HTTPException(422, "截止时间必须晚于当前时间")
    pids = [p.pid for p in payload.problems]
    if len(set(pids)) != len(pids) or not any(p.required for p in payload.problems):
        raise HTTPException(422, "题目不能重复，且至少设置一道必做题")
    problems = {p.pid: p for p in (await session.scalars(select(Problem).where(Problem.pid.in_(pids)))).all()}
    if len(problems) != len(pids):
        raise HTTPException(422, "部分题目不存在，请重新选择")
    members = await roster(session, user)
    if not any(m["class_name"] == payload.class_name for m in members):
        raise HTTPException(422, "这个班级暂无可布置训练的学生")
    valid_members = {m["id"] for m in members if m["class_name"] == payload.class_name}
    if payload.student_ids is not None and (len(set(payload.student_ids)) != len(payload.student_ids)
                                           or not set(payload.student_ids).issubset(valid_members)):
        raise HTTPException(422, "指定学生重复、不存在或不属于当前班级")
    if payload.source_training_id is not None:
        source = await accessible_training(session, user, payload.source_training_id)
        if source.status != "published" or source.class_name != payload.class_name or (row and row.id == source.id):
            raise HTTPException(422, "补练必须关联本班已发布的原训练")
        source_ids = set((await session.scalars(select(TrainingMember.student_id).where(TrainingMember.training_id == source.id))).all())
        if payload.student_ids is None or not set(payload.student_ids).issubset(source_ids):
            raise HTTPException(422, "补练必须明确选择原训练名单中的学生")
    if row is None:
        row = Training(teacher_id=user.id, original_teacher_id=user.id)
        session.add(row)
    row.class_id = group.id if group else None
    for key in ("title", "class_name", "objective", "description", "due_at", "student_ids", "source_training_id"):
        setattr(row, key, getattr(payload, key))
    await session.flush()
    await session.execute(delete(TrainingProblem).where(TrainingProblem.training_id == row.id))
    session.add_all([TrainingProblem(training_id=row.id, problem_id=problems[p.pid].id,
                                    position=i, required=p.required) for i, p in enumerate(payload.problems)])
    await session.commit()
    return await training_detail(session, user, row)


@router.post("")
async def create_training(payload: TrainingInput, session: SessionDep, user: TeacherUser):
    await training_write(session, user)
    return await save_draft(session, user, payload, None)


@router.get("/{training_id}")
async def get_training(training_id: ObjectPathId, session: SessionDep, user: CurrentUser):
    return await training_detail(session, user, await accessible_training(session, user, training_id))


@router.post("/{training_id}/draft")
async def edit_training(training_id: ObjectPathId, payload: TrainingInput, session: SessionDep, user: TeacherUser):
    await training_write(session, user)
    row = await accessible_training(session, user, training_id, lock=True)
    if row.status != "draft":
        raise HTTPException(409, "已发布的训练不能修改，请新建训练保留原记录")
    return await save_draft(session, user, payload, row)


@router.post("/{training_id}/publish")
async def publish_training(training_id: ObjectPathId, session: SessionDep, user: TeacherUser):
    await training_write(session, user)
    row = await accessible_training(session, user, training_id, lock=True)
    if row.status == "draft":
        if not await active_class(session, row.class_id):
            raise HTTPException(409, "班级已归档，请恢复后发布训练")
        if row.due_at <= datetime.now(UTC):
            raise HTTPException(409, "草稿已过截止时间，请先修改截止时间")
        students = (await session.scalars(select(User).where(
            User.role == "student", User.enabled.is_(True),
            User.class_id == row.class_id if row.class_id is not None else
            (User.class_id.is_(None) & (User.class_name == row.class_name))))).all()
        if row.student_ids is not None:
            students = [s for s in students if s.id in row.student_ids]
            if len(students) != len(row.student_ids):
                raise HTTPException(409, "指定学生的班级或账号已变化，请编辑名单后重新发布")
        if row.source_training_id is not None:
            await accessible_training(session, user, row.source_training_id)
        if not students:
            raise HTTPException(409, "班级暂无学生，无法发布")
        session.add_all([TrainingMember(training_id=row.id, student_id=s.id) for s in students])
        row.status = "published"
        row.published_at = datetime.now(UTC)
        await session.commit()
    return await training_detail(session, user, row)


@router.get("/{training_id}/analysis")
async def training_analysis(training_id: ObjectPathId, session: SessionDep, user: TeacherUser):
    row = await accessible_training(session, user, training_id)
    return await analyze_training(session, user, row)


@router.get("/{training_id}/lesson")
async def get_lesson(training_id: ObjectPathId, session: SessionDep, user: CurrentUser):
    return await TrainingLessonService(session, user).get(training_id)


@router.post("/{training_id}/lesson/generate")
async def generate_lesson(training_id: ObjectPathId, payload: LessonExpected, session: SessionDep, user: TeacherUser):
    return await TrainingLessonService(session, user).generate(training_id, payload.expected_revision)


@router.post("/{training_id}/lesson/draft")
async def save_lesson(training_id: ObjectPathId, payload: LessonSave, session: SessionDep, user: TeacherUser):
    return await TrainingLessonService(session, user).save(training_id, payload)


@router.post("/{training_id}/lesson/publish")
async def publish_lesson(training_id: ObjectPathId, payload: LessonExpected, session: SessionDep, user: TeacherUser):
    return await TrainingLessonService(session, user).publish(training_id, payload.expected_revision)


@router.get("/{training_id}/students/{student_id}/feedback")
async def get_feedback(training_id: ObjectPathId, student_id: ObjectPathId, session: SessionDep, user: CurrentUser):
    return await feedback_history(session, user, training_id, student_id)


@router.post("/{training_id}/students/{student_id}/feedback")
async def post_feedback(training_id: ObjectPathId, student_id: ObjectPathId, payload: FeedbackCreate, session: SessionDep, user: TeacherUser):
    return await create_feedback(session, user, training_id, student_id, payload)


@router.post("/{training_id}/feedback/{feedback_id}/read")
async def read_feedback(training_id: ObjectPathId, feedback_id: ObjectPathId, session: SessionDep, user: CurrentUser):
    return await mark_feedback_read(session, user, training_id, feedback_id)
