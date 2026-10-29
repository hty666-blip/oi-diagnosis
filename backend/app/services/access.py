"""提交与答疑对象的访问范围，统一供接口和服务使用。"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import HintSession, Submission, User


def submission_scope(user: User):
    """学生仅本人；教师仅本人及同名非空班级学生；管理员全部。"""
    if user.role == "admin":
        return true()
    own = Submission.student_id == user.id
    if user.role == "teacher" and (user.class_name or "").strip():
        students = select(User.id).where(
            User.role == "student", User.class_name == user.class_name
        )
        return or_(own, Submission.student_id.in_(students))
    return own


async def accessible_submission(
    session: AsyncSession, user: User, submission_id: int, *, lock: bool = False
) -> Submission:
    stmt = select(Submission).where(Submission.id == submission_id, submission_scope(user))
    if lock:
        stmt = stmt.with_for_update(of=Submission)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="提交不存在或无权访问")
    return row


async def accessible_hint_session(
    session: AsyncSession, user: User, session_id: int, *, lock: bool = False
) -> HintSession:
    stmt = (
        select(HintSession)
        .join(Submission, Submission.id == HintSession.submission_id)
        .where(
            HintSession.id == session_id,
            HintSession.student_id == Submission.student_id,
            submission_scope(user),
        )
    )
    if lock:
        stmt = stmt.with_for_update(of=HintSession)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")
    return row
