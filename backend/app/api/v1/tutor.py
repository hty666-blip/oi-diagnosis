"""答疑会话接口。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.infra.db import get_session
from app.services.tutor_service import TutorService

router = APIRouter(prefix="/sessions", tags=["tutor"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class SessionCreate(BaseModel):
    submission_id: int


class MessageIn(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)


class VerificationIn(BaseModel):
    submission_id: int = Field(..., gt=0)


@router.post("")
async def create_session(payload: SessionCreate, session: SessionDep, user: CurrentUser) -> dict:
    try:
        hs, turn = await TutorService(session, user).create(payload.submission_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"session_id": hs.id, "level": hs.level, "status": hs.status,
            "reply": turn.content, "action": "hint"}


@router.post("/{session_id}/messages")
async def send_message(session_id: int, payload: MessageIn, session: SessionDep,
                       user: CurrentUser) -> dict:
    try:
        return await TutorService(session, user).reply(session_id, payload.content)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/{session_id}")
async def get_session(session_id: int, session: SessionDep, user: CurrentUser) -> dict:
    try:
        return await TutorService(session, user).get(session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/{session_id}/verify")
async def verify_session(
    session_id: int, payload: VerificationIn, session: SessionDep, user: CurrentUser
) -> dict:
    return await TutorService(session, user).verify(session_id, payload.submission_id)
