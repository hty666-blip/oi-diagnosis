"""新班级使用编号授权；未迁移的旧导入记录仅在双方均无编号时兼容。"""
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import ClassTeacher, SchoolClass, Training, User


def teacher_class_scope(user: User, model=User):
    managed = model.class_id.in_(select(ClassTeacher.class_id).where(ClassTeacher.teacher_id == user.id))
    legacy = and_(model.class_id.is_(None), model.class_name == user.class_name,
                  bool(user.class_id is None and (user.class_name or "").strip()))
    return or_(managed, legacy)


def student_class_scope(student: User, model=Training):
    if student.class_id is not None:
        return model.class_id == student.class_id
    return and_(model.class_id.is_(None), model.class_name == student.class_name,
                bool((student.class_name or "").strip()))


async def same_class(session: AsyncSession, user: User, row: User | Training) -> bool:
    if user.role == "admin":
        return True
    if user.role == "teacher":
        if row.class_id is not None:
            return await session.get(ClassTeacher, (row.class_id, user.id)) is not None
        return user.class_id is None and bool(user.class_name.strip()) and row.class_name == user.class_name
    return ((user.class_id is not None and row.class_id == user.class_id) or
            (user.class_id is None and row.class_id is None and bool(user.class_name.strip()) and row.class_name == user.class_name))


async def active_class(session: AsyncSession, class_id: int | None):
    if class_id is not None:
        row = await session.get(SchoolClass, class_id)
        return row is not None and not row.archived
    return True
