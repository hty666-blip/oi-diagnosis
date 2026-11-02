"""训练草稿、发布及授权完成矩阵。"""
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AwareDatetime, BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, TeacherUser
from app.api.input_types import ObjectPathId
from app.domain.models import Problem, Training, TrainingMember, TrainingProblem, User
from app.infra.db import get_session
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

    @field_validator("title", "class_name", "objective", "description")
    @classmethod
    def valid_text(cls, value: str):
        if "\x00" in value:
            raise ValueError("内容不能含空字符")
        value.encode("utf-8")
        return value.strip()


@router.get("/roster")
async def roster(session: SessionDep, user: TeacherUser):
    stmt = select(User).where(User.role == "student", User.class_name != "")
    if user.role != "admin":
        stmt = stmt.where(User.class_name == user.class_name) if user.class_name.strip() else stmt.where(False)
    students = (await session.scalars(stmt.order_by(User.class_name, User.id))).all()
    return [{"id": s.id, "name": s.display_name or s.username, "class_name": s.class_name} for s in students]


@router.get("")
async def list_trainings(session: SessionDep, user: CurrentUser):
    stmt = select(Training).order_by(Training.id.desc())
    if user.role == "student":
        stmt = stmt.join(TrainingMember, TrainingMember.training_id == Training.id).where(
            TrainingMember.student_id == user.id, Training.status == "published", Training.class_name == user.class_name)
    elif user.role == "teacher":
        stmt = stmt.where(Training.teacher_id == user.id, Training.class_name == user.class_name)
    return [training_out(row) for row in (await session.scalars(stmt.limit(100))).all()]


async def save_draft(session: AsyncSession, user: User, payload: TrainingInput, row: Training | None):
    if not payload.title or not payload.class_name:
        raise HTTPException(422, "请填写训练名称和班级")
    if user.role != "admin" and payload.class_name != user.class_name:
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
    if row is None:
        row = Training(teacher_id=user.id)
        session.add(row)
    for key in ("title", "class_name", "objective", "description", "due_at"):
        setattr(row, key, getattr(payload, key))
    await session.flush()
    await session.execute(delete(TrainingProblem).where(TrainingProblem.training_id == row.id))
    session.add_all([TrainingProblem(training_id=row.id, problem_id=problems[p.pid].id,
                                    position=i, required=p.required) for i, p in enumerate(payload.problems)])
    await session.commit()
    return await training_detail(session, user, row)


@router.post("")
async def create_training(payload: TrainingInput, session: SessionDep, user: TeacherUser):
    return await save_draft(session, user, payload, None)


@router.get("/{training_id}")
async def get_training(training_id: ObjectPathId, session: SessionDep, user: CurrentUser):
    return await training_detail(session, user, await accessible_training(session, user, training_id))


@router.post("/{training_id}/draft")
async def edit_training(training_id: ObjectPathId, payload: TrainingInput, session: SessionDep, user: TeacherUser):
    row = await accessible_training(session, user, training_id, lock=True)
    if row.status != "draft":
        raise HTTPException(409, "已发布的训练不能修改，请新建训练保留原记录")
    return await save_draft(session, user, payload, row)


@router.post("/{training_id}/publish")
async def publish_training(training_id: ObjectPathId, session: SessionDep, user: TeacherUser):
    row = await accessible_training(session, user, training_id, lock=True)
    if row.status == "draft":
        if row.due_at <= datetime.now(UTC):
            raise HTTPException(409, "草稿已过截止时间，请先修改截止时间")
        students = (await session.scalars(select(User).where(
            User.role == "student", User.class_name == row.class_name))).all()
        if not students:
            raise HTTPException(409, "班级暂无学生，无法发布")
        session.add_all([TrainingMember(training_id=row.id, student_id=s.id) for s in students])
        row.status = "published"
        row.published_at = datetime.now(UTC)
        await session.commit()
    return await training_detail(session, user, row)
