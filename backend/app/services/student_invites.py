"""班级邀请码的签发与一次性注册。"""

import hashlib
import secrets
from asyncio import to_thread
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select

from app.core.security import hash_password
from app.domain.models import SchoolClass, StudentInvite, User
from app.services.admin_service import audit, management_lock

INVITE_DURATION = timedelta(hours=24)
UNAVAILABLE = "邀请码无效、已使用或已过期。已注册的学生可直接登录。"


def now() -> datetime:
    return datetime.now(UTC)


def digest(code: str) -> str:
    return hashlib.sha256(code.encode("ascii")).hexdigest()


def invite_out(invite: StudentInvite, instant: datetime) -> dict:
    if invite.used_by_id is not None:
        state = "used"
    elif invite.revoked_at is not None:
        state = "revoked"
    elif invite.expires_at <= instant:
        state = "expired"
    else:
        state = "active"
    return {"id": invite.id, "created_at": invite.created_at, "expires_at": invite.expires_at, "state": state}


async def issue_invite(session, actor: User, class_id: int):
    group = await session.scalar(select(SchoolClass).where(SchoolClass.id == class_id).with_for_update())
    if group is None or group.archived:
        raise HTTPException(404, "班级不存在或已归档")
    code = secrets.token_urlsafe(24)
    instant = now().replace(microsecond=0)
    invite = StudentInvite(class_id=class_id, created_by_id=actor.id, code_digest=digest(code),
                           created_at=instant, expires_at=instant + INVITE_DURATION)
    session.add(invite)
    await session.flush()
    audit(session, actor, "class.invite.issue", f"invite:{invite.id}", {}, {
        "class_id": class_id, "invite_created_at": instant.isoformat(),
    })
    await session.commit()
    return {**invite_out(invite, instant), "code": code}


async def redeem_invite(session, *, code: str, username: str, display_name: str, password: str) -> User:
    active = select(StudentInvite).where(
        StudentInvite.code_digest == digest(code), StudentInvite.used_by_id.is_(None),
        StudentInvite.revoked_at.is_(None), StudentInvite.expires_at > now(),
    )
    ident = await session.scalar(active.with_only_columns(StudentInvite.id))
    if ident is None:
        raise HTTPException(409, UNAVAILABLE)
    hashed = await to_thread(hash_password, password)
    await management_lock(session)
    invite = await session.scalar(select(StudentInvite).where(StudentInvite.id == ident).with_for_update())
    if invite is None or invite_out(invite, now())["state"] != "active":
        raise HTTPException(409, UNAVAILABLE)
    group = await session.scalar(select(SchoolClass).where(SchoolClass.id == invite.class_id).with_for_update())
    if group is None or group.archived:
        raise HTTPException(409, UNAVAILABLE)
    if await session.scalar(select(User.id).where(User.username == username).with_for_update()) is not None:
        raise HTTPException(409, "用户名已使用，请换一个用户名")
    student = User(username=username, display_name=display_name, password_hash=hashed, role="student",
                   class_id=group.id, class_name=group.name)
    session.add(student)
    await session.flush()
    invite.used_by_id = student.id
    audit(session, student, "student.register", f"user:{student.id}", {}, {
        "class_id": group.id, "invite_id": invite.id, "invite_created_at": invite.created_at.isoformat(),
    })
    await session.commit()
    return student
