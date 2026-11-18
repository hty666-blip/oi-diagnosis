"""发布班级初赛专项，读取名单范围内的进度与固定的题面。"""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep, TeacherUser
from app.api.input_types import ObjectId, ObjectPathId
from app.api.v1.written_questions import PracticeStudent, question_out, read_image, summary
from app.core.text_validation import StorageText
from app.domain.models import (
    SchoolClass,
    User,
    WrittenAssignment,
    WrittenAssignmentMember,
    WrittenAssignmentQuestion,
    WrittenQuestion,
)
from app.services.admin_service import audit, training_write
from app.services.written_assignments import (
    accessible_assignment,
    active_students,
    assignment_detail,
    assignment_summary,
    assignment_version,
    managed_classes,
    scoped_assignments,
)
from app.services.written_bank_storage import load_snapshot
from app.services.written_review import visible_query

router = APIRouter(prefix="/written/assignments", tags=["written-assignments"])


class AssignmentQuestionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: ObjectId
    version_id: UUID


class AssignmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    class_id: ObjectId
    request_id: UUID
    title: StorageText = Field(min_length=1, max_length=120)
    due_at: AwareDatetime
    questions: list[AssignmentQuestionInput] = Field(min_length=1, max_length=10)
    student_ids: list[ObjectId] | None = Field(default=None, min_length=1, max_length=200)


@router.get("/classes")
async def assignment_classes(response: Response, session: SessionDep, user: TeacherUser):
    response.headers["Cache-Control"] = "no-store"
    groups = await session.scalars(
        managed_classes(user).where(SchoolClass.archived.is_(False)).order_by(SchoolClass.name)
    )
    return [{"id": group.id, "name": group.name} for group in groups]


@router.get("/classes/{class_id}/students")
async def assignment_students(class_id: ObjectPathId, response: Response, session: SessionDep, user: TeacherUser):
    response.headers["Cache-Control"] = "no-store"
    group = await session.scalar(managed_classes(user).with_only_columns(SchoolClass.id).where(
        SchoolClass.id == class_id, SchoolClass.archived.is_(False),
    ))
    if group is None:
        raise HTTPException(404, "班级不存在或无权查看名单")
    students = await session.execute(active_students(group).with_only_columns(
        User.id, User.display_name, User.username,
    ).order_by(User.display_name, User.id))
    return [{"id": ident, "name": name or username, "username": username} for ident, name, username in students]


@router.get("")
async def assignments(response: Response, session: SessionDep, user: CurrentUser):
    response.headers["Cache-Control"] = "no-store"
    rows = await session.execute(scoped_assignments(user).order_by(WrittenAssignment.id.desc()).limit(100))
    return [assignment_summary(assignment, group) for assignment, group in rows]


@router.post("")
async def publish_assignment(body: AssignmentInput, response: Response, session: SessionDep, user: TeacherUser):
    response.headers["Cache-Control"] = "no-store"
    # 未指定名单时保留旧版全班请求的摘要，兼容已有发布回执。
    content = body.model_dump(mode="json", exclude={"request_id"}, exclude_none=True)
    selected = body.student_ids
    if selected is not None:
        if len(set(selected)) != len(selected):
            raise HTTPException(422, "指定学生不能重复")
        content["student_ids"] = sorted(selected)
    digest = hashlib.sha256(json.dumps(content, sort_keys=True).encode("ascii")).hexdigest()
    await training_write(session, user)
    prior = await session.scalar(select(WrittenAssignment).where(
        WrittenAssignment.teacher_id == user.id, WrittenAssignment.request_id == str(body.request_id)
    ))
    if prior is not None:
        if prior.request_digest != digest:
            raise HTTPException(409, "该请求编号已用于另一份初赛任务")
        assignment, group = await accessible_assignment(session, user, prior.id)
        result = assignment_summary(assignment, group)
        await session.commit()
        return {**result, "replayed": True}

    group = await session.scalar(managed_classes(user).where(SchoolClass.id == body.class_id))
    if group is None:
        raise HTTPException(404, "班级不存在或无权布置任务")
    if group.archived:
        raise HTTPException(409, "班级已归档")
    if body.due_at <= datetime.now(UTC):
        raise HTTPException(422, "截止时间必须晚于当前时间")
    ids = [question.id for question in body.questions]
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "专项题目不能重复")
    visible = await session.execute(visible_query().where(WrittenQuestion.id.in_(ids)))
    by_id = {question.id: (question, version) for question, version in visible}
    for item in body.questions:
        row = by_id.get(item.id)
        if row is None or row[1].id != str(item.version_id):
            raise HTTPException(409, "选题已下架或有新版，请刷新题库重新选题")
    student_query = active_students(group.id).with_only_columns(User.id)
    if selected is not None:
        student_query = student_query.where(User.id.in_(selected))
    students = list(await session.scalars(student_query))
    if selected is not None and len(students) != len(selected):
        raise HTTPException(409, {"code": "written_assignment_roster_changed", "message": "所选学生已转班、停用或不在本班，请刷新名单后重新选择"})
    if not students:
        raise HTTPException(409, "该班级暂无启用的学生")
    assignment = WrittenAssignment(
        class_id=group.id, teacher_id=user.id, title=body.title, due_at=body.due_at,
        request_id=str(body.request_id), request_digest=digest,
    )
    session.add(assignment)
    await session.flush()
    session.add_all([
        WrittenAssignmentQuestion(
            assignment_id=assignment.id, question_id=ident, version_id=by_id[ident][1].id,
            position=position, summary=summary(*by_id[ident]),
        )
        for position, ident in enumerate(ids)
    ])
    session.add_all([
        WrittenAssignmentMember(assignment_id=assignment.id, student_id=ident) for ident in students
    ])
    audit(session, user, "written_assignment.publish", str(assignment.id), {}, {
        "class_id": group.id, "question_count": len(ids), "student_count": len(students),
        "audience": "class" if selected is None else "selected",
    })
    await session.commit()
    return {**assignment_summary(assignment, group), "replayed": False}


@router.get("/{assignment_id}")
async def get_assignment(assignment_id: ObjectPathId, response: Response, session: SessionDep, user: CurrentUser):
    response.headers["Cache-Control"] = "no-store"
    return await assignment_detail(session, user, *await accessible_assignment(session, user, assignment_id))


@router.get("/{assignment_id}/questions/{question_id}")
async def get_question(
    assignment_id: ObjectPathId, question_id: ObjectPathId, response: Response,
    session: SessionDep, user: PracticeStudent,
):
    response.headers["Cache-Control"] = "no-store"
    version = await assignment_version(session, user, assignment_id, question_id)
    snapshot = await load_snapshot(session, version.batch_id, version.snapshot_metadata)
    images = [
        f"/api/v1/written/assignments/{assignment_id}/questions/{question_id}/assets/{i}"
        for i in range(len(snapshot.get("asset_paths", [])))
    ]
    return question_out(version, snapshot, images)


@router.get("/{assignment_id}/questions/{question_id}/assets/{index}")
async def get_question_image(
    assignment_id: ObjectPathId, question_id: ObjectPathId, index: int,
    session: SessionDep, user: PracticeStudent,
):
    return await read_image(session, await assignment_version(session, user, assignment_id, question_id), index)
