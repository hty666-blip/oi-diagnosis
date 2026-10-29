"""教师审定与学生可见的复核记录。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, SessionDep, TeacherUser
from app.services.review_service import ReviewCreate, ReviewService

router = APIRouter(tags=["reviews"])


@router.post("/coach/reviews")
async def create_review(payload: ReviewCreate, session: SessionDep, user: TeacherUser) -> dict:
    return await ReviewService(session, user).create(payload)


@router.get("/submissions/{submission_id}/reviews")
async def review_history(submission_id: int, session: SessionDep, user: CurrentUser) -> dict:
    return await ReviewService(session, user).history(submission_id)
