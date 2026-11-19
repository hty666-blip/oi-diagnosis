"""从班级专项复核简答，确认后向作答学生发布分数和反馈。"""

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from app.agents.written_short_grading import suggest
from app.api.deps import SessionDep, TeacherUser
from app.api.input_types import ObjectPathId
from app.api.v1.written_questions import attempt_query, question_out, read_image
from app.core.text_validation import StorageText
from app.domain.models import (
    User,
    WrittenAssignmentAttempt,
    WrittenAssignmentMember,
    WrittenAttempt,
    WrittenShortDraft,
    WrittenShortGrade,
)
from app.services.admin_service import audit, training_write
from app.services.runtime_ai import observe_call, runtime_client
from app.services.written_assignments import accessible_assignment, active_students
from app.services.written_bank_storage import load_snapshot


def prevent_cache(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(
    prefix="/written/assignments/{assignment_id}/short-reviews",
    tags=["written-short-reviews"], dependencies=[Depends(prevent_cache)],
)
Points = Annotated[int, Field(strict=True, ge=0, le=100)]


class ConfirmGrade(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    points: list[Points] = Field(min_length=1, max_length=8)
    feedback: StorageText = Field(min_length=3, max_length=3000)


async def scoped_attempt(session, user, assignment_id, attempt_id):
    _, group = await accessible_assignment(session, user, assignment_id)
    students = active_students(group.id).with_only_columns(User.id).join(
        WrittenAssignmentMember, WrittenAssignmentMember.student_id == User.id,
    ).where(WrittenAssignmentMember.assignment_id == assignment_id)
    row = (await session.execute(
        attempt_query().join(WrittenAssignmentAttempt, WrittenAssignmentAttempt.attempt_id == WrittenAttempt.id)
        .where(WrittenAttempt.id == attempt_id, WrittenAssignmentAttempt.assignment_id == assignment_id,
               WrittenAttempt.student_id.in_(students),
               WrittenAttempt.question_summary["question_type"].as_string() == "short_answer")
        .execution_options(populate_existing=True)
    )).first()
    if row is None:
        raise HTTPException(404, "这份简答作答不存在或不属于当前班级专项")
    return row[0], row[1], group


def grade_out(grade):
    return {"points": grade.points, "feedback": grade.feedback, "graded_at": grade.created_at}


@router.get("/{attempt_id}")
async def review(assignment_id: ObjectPathId, attempt_id: ObjectPathId, session: SessionDep, user: TeacherUser):
    attempt, version, group = await scoped_attempt(session, user, assignment_id, attempt_id)
    data = await load_snapshot(session, version.batch_id, version.snapshot_metadata)
    images = [f"/api/v1/written/assignments/{assignment_id}/short-reviews/{attempt_id}/assets/{i}"
              for i in range(len(data.get("asset_paths", [])))]
    grade = await session.get(WrittenShortGrade, attempt.id)
    draft = await session.get(WrittenShortDraft, attempt.id) if grade is None else None
    return {
        "question": question_out(version, data, images),
        "response": attempt.response["value"],
        "answer": data["answer"],
        "grade": grade_out(grade) if grade else None,
        "read_only": group.archived,
        "suggestion": draft.suggestion if draft else None,
        "can_suggest": supports_suggestion(data),
    }


@router.get("/{attempt_id}/assets/{index}")
async def review_image(assignment_id: ObjectPathId, attempt_id: ObjectPathId, index: int,
                       session: SessionDep, user: TeacherUser):
    _, version, _ = await scoped_attempt(session, user, assignment_id, attempt_id)
    return await read_image(session, version, index)


def supports_suggestion(snapshot):
    return not (snapshot.get("asset_paths") or len(snapshot["stem"]) > 6000 or len(snapshot["shared_stem"]) > 8000)


@router.post("/{attempt_id}/suggestion")
async def draft_grade(assignment_id: ObjectPathId, attempt_id: ObjectPathId, session: SessionDep, user: TeacherUser):
    await training_write(session, user)
    attempt, version, group = await scoped_attempt(session, user, assignment_id, attempt_id)
    if group.archived or attempt.correct is not None:
        raise HTTPException(409, "此作答已确认评分或班级已归档")
    draft = await session.get(WrittenShortDraft, attempt.id, populate_existing=True)
    instant = datetime.now(UTC)
    if draft and draft.suggestion:
        return draft.suggestion
    if draft and draft.lease_until > instant:
        raise HTTPException(409, "草评正在处理或刚失败，请稍候再查看")
    snapshot = await load_snapshot(session, version.batch_id, version.snapshot_metadata)
    if not supports_suggestion(snapshot):
        raise HTTPException(422, "含图片或长共用题干的简答请直接人工复核")
    client = await runtime_client(session)
    if client is None or not client.available:
        raise HTTPException(503, "平台模型不可用，可以直接给分")
    claim = str(uuid4())
    if draft is None:
        draft = WrittenShortDraft(attempt_id=attempt.id)
        session.add(draft)
    draft.claim = claim
    draft.lease_until = instant + timedelta(seconds=35)
    answer = attempt.response["value"]
    await session.commit()

    async def observe(feature, used, reason):
        await observe_call(session, client, feature, used=used, reason=reason)

    suggestion = await suggest(client, snapshot, answer, observe)
    if suggestion is None:
        raise HTTPException(503, "草评未通过证据核对或模型暂不可用，可以直接给分")

    await training_write(session, user)
    attempt, _, group = await scoped_attempt(session, user, assignment_id, attempt_id)
    draft = await session.get(WrittenShortDraft, attempt.id, populate_existing=True)
    if (group.archived or attempt.correct is not None or draft is None
            or draft.claim != claim or draft.lease_until <= datetime.now(UTC)):
        raise HTTPException(409, "作答已确认评分或草评已过期，请重新读取")
    draft.suggestion = suggestion
    await session.commit()
    return suggestion


@router.post("/{attempt_id}")
async def confirm(assignment_id: ObjectPathId, attempt_id: ObjectPathId, body: ConfirmGrade,
                  session: SessionDep, user: TeacherUser):
    await training_write(session, user)
    attempt, version, group = await scoped_attempt(session, user, assignment_id, attempt_id)
    prior = await session.get(WrittenShortGrade, attempt.id)
    if prior:
        if prior.points != body.points or prior.feedback != body.feedback:
            raise HTTPException(409, "这份评分已确认，请重新读取已发布结果")
        return grade_out(prior)
    if group.archived:
        raise HTTPException(409, "班级已归档")
    if not body.feedback.strip():
        raise HTTPException(422, "请填写评分反馈")
    data = await load_snapshot(session, version.batch_id, version.snapshot_metadata)
    rubric = data["answer"]["rubric"]
    if len(body.points) != len(rubric) or any(
        awarded > item["points"] for awarded, item in zip(body.points, rubric, strict=True)
    ):
        raise HTTPException(422, "请逐项给分，且不得超过各评分要点的分值")
    score = sum(body.points)
    grade = WrittenShortGrade(attempt_id=attempt.id, teacher_id=user.id, points=body.points, feedback=body.feedback)
    session.add(grade)
    attempt.correct = score == attempt.max_score
    attempt.earned_score = score
    audit(session, user, "written_short_grade.confirm", str(attempt.id), {},
          {"assignment_id": assignment_id, "score": score, "max_score": attempt.max_score})
    await session.commit()
    return grade_out(grade)
