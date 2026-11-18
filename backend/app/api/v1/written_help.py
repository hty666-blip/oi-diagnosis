"""保存学生错题追问和两位 Agent 的交接记录。"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.written_help import WrittenEvidence, explain
from app.api.deps import SessionDep
from app.api.input_types import MAX_OBJECT_ID, ObjectId, ObjectPathId
from app.api.v1 import written_exams
from app.api.v1.written_questions import PracticeStudent, owned_attempt, submission_conflict
from app.core.text_validation import StorageText
from app.domain.models import (
    User,
    WrittenAttempt,
    WrittenExam,
    WrittenExamHelpTurn,
    WrittenExamItem,
    WrittenHelpTurn,
    WrittenQuestionVersion,
)
from app.gateway.llm import LLMClient
from app.services.runtime_ai import observe_call, runtime_client
from app.services.written_bank_storage import load_snapshot
from app.services.written_history import earlier_practice
from app.services.written_knowledge import help_knowledge

router = APIRouter(prefix="/written/attempts", tags=["written-help"])
exam_router = APIRouter(prefix="/written/exams", tags=["written-help"])
PAGE_SIZE = 12
CLAIM_TTL = timedelta(seconds=55)


class HelpInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    after_id: ObjectId | None = None
    message: StorageText = Field(min_length=1, max_length=1200)

    @field_validator("message")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请输入要问的内容")
        return value.strip()


def completed_turn(row: WrittenHelpTurn | WrittenExamHelpTurn) -> dict:
    return {
        "id": row.id,
        "request_id": row.request_id,
        "question": row.question,
        "created_at": row.created_at,
        **row.result,
    }


@dataclass(frozen=True)
class HelpThread:
    source: WrittenAttempt | WrittenExamItem
    version: WrittenQuestionVersion
    exam: WrittenExam | None = None

    @property
    def model(self):
        return WrittenHelpTurn if self.exam is None else WrittenExamHelpTurn

    @property
    def key(self):
        if self.exam is None:
            return {"attempt_id": self.source.id}
        return {"exam_id": self.exam.id, "question_id": self.source.question_id}

    async def lock(self, session: AsyncSession, student_id: int):
        if self.exam is not None:
            await written_exams.owned(session, student_id, self.exam.id, lock=True)
            return
        await lock_attempt(session, self.source.id, student_id)

    @property
    def history_cutoff(self):
        if self.exam is None:
            return WrittenAttempt.id < self.source.id
        return WrittenAttempt.created_at < self.exam.submitted_at


async def attempt_thread(session: AsyncSession, student_id: int, attempt_id: int):
    attempt, version = await owned_attempt(session, student_id, attempt_id)
    if attempt.question_summary["question_type"] == "short_answer":
        raise HTTPException(409, "简答题请查看班级任务中的教师反馈")
    if attempt.correct:
        raise HTTPException(409, "请从答错的作答记录打开错题答疑")
    return HelpThread(attempt, version)


async def exam_thread(session: AsyncSession, student_id: int, exam_id: int, question_id: int):
    exam = await written_exams.owned(session, student_id, exam_id)
    if exam.submitted_at is None:
        raise HTTPException(409, "交卷后才能使用原卷错题答疑")
    item, version = await written_exams.frozen_question(session, exam, question_id)
    if item.response is None or item.earned_score is None or item.earned_score >= item.max_score:
        raise HTTPException(409, "请选择原卷中已作答且答错的题目")
    return HelpThread(item, version, exam)


@router.get("/{attempt_id}/help")
async def history(
    attempt_id: ObjectPathId,
    session: SessionDep,
    user: PracticeStudent,
    response: Response,
    before_id: int | None = Query(None, ge=1, le=MAX_OBJECT_ID),
):
    thread = await attempt_thread(session, user.id, attempt_id)
    return await read_thread(session, thread, response, before_id)


@exam_router.get("/{exam_id}/questions/{question_id}/help")
async def exam_history(
    exam_id: ObjectPathId, question_id: ObjectPathId, session: SessionDep, user: PracticeStudent,
    response: Response, before_id: int | None = Query(None, ge=1, le=MAX_OBJECT_ID),
):
    thread = await exam_thread(session, user.id, exam_id, question_id)
    return await read_thread(session, thread, response, before_id)


async def read_thread(session: AsyncSession, thread: HelpThread, response: Response, before_id: int | None):
    model = thread.model
    query = select(model).filter_by(**thread.key)
    ready = query.where(model.result.is_not(None))
    if before_id is not None:
        ready = ready.where(model.id < before_id)
    rows = list(
        await session.scalars(ready.order_by(model.id.desc()).limit(PAGE_SIZE + 1))
    )
    pending = None
    if before_id is None:
        latest = await session.scalar(query.order_by(model.id.desc()).limit(1))
        if latest is not None and latest.result is None:
            pending = {
                "request_id": latest.request_id,
                "message": latest.question,
                "retry_at": latest.lease_until,
            }
    response.headers["Cache-Control"] = "no-store"
    page = rows[:PAGE_SIZE]
    return {
        "items": [completed_turn(row) for row in reversed(page)],
        "before_id": page[-1].id if len(rows) > PAGE_SIZE else None,
        "pending": pending,
    }


async def lock_attempt(session: AsyncSession, attempt_id: int, student_id: int) -> None:
    present = await session.scalar(
        select(WrittenAttempt.id)
        .where(WrittenAttempt.id == attempt_id, WrittenAttempt.student_id == student_id)
        .with_for_update()
    )
    if present is None:
        raise HTTPException(404, "作答记录不存在")


def timeout_conflict():
    return submission_conflict("written_help_busy", "该题有一条追问在处理中，请稍后读取答疑记录")


async def check_actor(session: AsyncSession, user: User, token_version: int) -> None:
    await session.refresh(user, with_for_update=True)
    if (
        not user.enabled
        or user.role != "student"
        or user.must_change_password
        or user.token_version != token_version
    ):
        raise HTTPException(401, "账号状态已变化，请重新登录")


@router.post("/{attempt_id}/help")
async def ask(
    attempt_id: ObjectPathId,
    body: HelpInput,
    session: SessionDep,
    user: PracticeStudent,
    request: Request,
    response: Response,
):
    thread = await attempt_thread(session, user.id, attempt_id)
    return await ask_thread(session, user, thread, body, request, response)


@exam_router.post("/{exam_id}/questions/{question_id}/help")
async def ask_exam(
    exam_id: ObjectPathId, question_id: ObjectPathId, body: HelpInput, session: SessionDep,
    user: PracticeStudent, request: Request, response: Response,
):
    thread = await exam_thread(session, user.id, exam_id, question_id)
    return await ask_thread(session, user, thread, body, request, response)


async def ask_thread(
    session: AsyncSession, user: User, thread: HelpThread, body: HelpInput, request: Request, response: Response,
):
    student_id = user.id
    await check_actor(session, user, request.state.token_version)
    await thread.lock(session, student_id)
    model = thread.model
    query = select(model).filter_by(**thread.key)
    request_id = str(body.request_id)
    existing = await session.scalar(query.where(model.request_id == request_id))
    if existing is not None:
        if existing.question != body.message:
            raise submission_conflict("written_help_request_conflict", "该请求编号已用于其他追问")
        if existing.result is not None:
            response.headers["Cache-Control"] = "no-store"
            return {**completed_turn(existing), "replayed": True}
    now = datetime.now(UTC)
    latest = await session.scalar(query.order_by(model.id.desc()).limit(1))
    if latest is not None and latest.result is None and latest.lease_until > now:
        raise timeout_conflict()
    if existing is not None and (latest is None or existing.id != latest.id):
        raise submission_conflict("written_help_changed", "答疑已有更新，请刷新记录后再追问")
    previous = list(
        await session.scalars(
            query.where(model.result.is_not(None))
            .order_by(model.id.desc())
            .limit(3)
        )
    )
    if body.after_id != (previous[0].id if previous else None):
        raise submission_conflict("written_help_changed", "答疑已有更新，请刷新记录后再追问")

    token = str(uuid4())
    turn = existing or model(**thread.key, request_id=request_id, question=body.message)
    turn.claim_token = token
    turn.lease_until = now + CLAIM_TTL
    session.add(turn)
    await session.flush()
    turn_id = turn.id
    prior = [
        {"question": row.question[:1000], "reply": row.result["tutor"]["reply"][:1000]}
        for row in reversed(previous)
    ]
    # 预约已落库。先释放作答行锁，再读取材料并调用模型。
    await session.commit()
    source, version = thread.source, thread.version
    snapshot = await load_snapshot(session, version.batch_id, version.snapshot_metadata)
    knowledge, managed = await help_knowledge(session, version.batch_id, snapshot)
    practice = await earlier_practice(session, student_id, source.question_id, version.batch_id, snapshot,
                                      before=thread.history_cutoff)
    evidence = WrittenEvidence.from_snapshot(
        snapshot,
        source.response,
        source.earned_score,
        source.max_score,
        knowledge=knowledge,
        use_demo_card=not managed,
        prior_practice=practice,
    )
    client = await runtime_client(session, factory=lambda **kwargs: LLMClient(timeout=12, **kwargs))
    await session.commit()

    async def observe(feature: str, used: bool, reason: str):
        await observe_call(session, client, feature, used=used, reason=reason)

    result = await explain(evidence, body.message, prior, client, observe)

    await check_actor(session, user, request.state.token_version)
    await thread.lock(session, student_id)
    latest = await session.scalar(
        query.order_by(model.id.desc()).limit(1).execution_options(populate_existing=True)
    )
    if latest is None or latest.id != turn_id or latest.claim_token != token:
        raise timeout_conflict()
    latest.result = result
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return {**completed_turn(latest), "replayed": False}
