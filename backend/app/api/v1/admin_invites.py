"""管理员签发与撤销班级学生邀请码。"""

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select

from app.api.deps import AdminUser, SessionDep
from app.api.input_types import ObjectPathId
from app.domain.models import SchoolClass, StudentInvite
from app.services.admin_service import admin_write, audit
from app.services.student_invites import invite_out, issue_invite, now

router = APIRouter(prefix="/admin/classes", tags=["admin-student-invites"])


@router.get("/{class_id}/student-invites")
async def invites(class_id: ObjectPathId, response: Response, session: SessionDep, user: AdminUser):
    response.headers["Cache-Control"] = "no-store"
    group = await session.get(SchoolClass, class_id)
    if group is None:
        raise HTTPException(404, "班级不存在")
    rows = await session.scalars(select(StudentInvite).where(StudentInvite.class_id == class_id)
                                 .order_by(StudentInvite.id.desc()).limit(25))
    instant = now()
    return {"archived": group.archived, "items": [invite_out(row, instant) for row in rows]}


@router.post("/{class_id}/student-invites", status_code=201)
async def issue(class_id: ObjectPathId, request: Request, response: Response, session: SessionDep, user: AdminUser):
    response.headers["Cache-Control"] = "no-store"
    await admin_write(session, user, request)
    return await issue_invite(session, user, class_id)


@router.post("/{class_id}/student-invites/{invite_id}/revoke")
async def revoke(class_id: ObjectPathId, invite_id: ObjectPathId, request: Request, response: Response,
                 session: SessionDep, user: AdminUser):
    response.headers["Cache-Control"] = "no-store"
    await admin_write(session, user, request)
    invite = await session.scalar(select(StudentInvite).where(
        StudentInvite.id == invite_id, StudentInvite.class_id == class_id,
    ).with_for_update())
    if invite is None:
        raise HTTPException(404, "邀请码不存在")
    instant = now()
    if invite_out(invite, instant)["state"] == "active":
        invite.revoked_at = instant
        audit(session, user, "class.invite.revoke", f"invite:{invite.id}", {}, {
            "class_id": class_id, "invite_created_at": invite.created_at.isoformat(),
        })
    await session.commit()
    return invite_out(invite, instant)
