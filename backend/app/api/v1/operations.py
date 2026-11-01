"""教师与管理员授权范围内的后台判题摘要。"""

from fastapi import APIRouter

from app.api.deps import SessionDep, TeacherUser
from app.services.operations_service import OperationsService

router = APIRouter(tags=["operations"])


@router.get("/coach/judging")
async def judging_operations(session: SessionDep, user: TeacherUser) -> dict:
    """授权范围内的任务摘要；等待从可执行时间起算，不含未来退避或整次运行耗时。"""
    return await OperationsService(session, user).judging()
