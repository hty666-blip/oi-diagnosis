"""学生训练档案和受保护报告导出。"""
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.api.input_types import ObjectPathId
from app.infra.db import get_session
from app.services.student_followup_service import profile_markdown, student_profile

router = APIRouter(prefix="/students", tags=["students"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/{student_id}/profile")
async def get_profile(student_id: ObjectPathId, session: SessionDep, user: CurrentUser):
    return await student_profile(session, user, student_id)


@router.get("/{student_id}/profile/report")
async def get_profile_report(student_id: ObjectPathId, session: SessionDep, user: CurrentUser):
    report = profile_markdown(await student_profile(session, user, student_id))
    return Response(report, media_type="text/markdown; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="student-training-{student_id}.md"', "Cache-Control": "no-store"})
