"""学生训练档案与个别反馈，反馈后的进展由新增提交事实派生。"""
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.input_types import ObjectId
from app.core.text_validation import StorageText
from app.domain.models import (
    JudgeJob,
    Problem,
    Submission,
    Training,
    TrainingFeedback,
    TrainingMember,
    TrainingProblem,
    User,
    WrittenAttempt,
    WrittenExam,
)
from app.services.class_access import same_class, student_class_scope
from app.services.problem_versions import resolve_many
from app.services.training_service import accessible_training, submission_cell, training_out
from app.services.written_exam_review import summarize_exam_cards

PROFILE_WRITTEN_ATTEMPTS = 100
PROFILE_WRITTEN_EXAMS = 5


class FeedbackCreate(BaseModel):
    content: StorageText = Field(min_length=1, max_length=4000)
    submission_id: ObjectId | None = None
    request_id: UUID


async def accessible_student(session: AsyncSession, user: User, student_id: int) -> User:
    student = await session.get(User, student_id)
    allowed = student is not None and student.role == "student"
    if user.role == "student":
        allowed = allowed and student_id == user.id
    elif user.role == "teacher":
        allowed = allowed and await same_class(session, user, student)
    elif user.role != "admin":
        allowed = False
    if not allowed:
        raise HTTPException(404, "学生不存在或无权访问")
    return student


async def require_member(session: AsyncSession, user: User, training_id: int, student_id: int, *, lock=False):
    training = await accessible_training(session, user, training_id, lock=lock)
    student = await accessible_student(session, user, student_id)
    if (training.status != "published" or not await same_class(session, student, training)
            or await session.get(TrainingMember, (training_id, student_id)) is None):
        raise HTTPException(404, "学生不在当前训练名单中")
    return training, student


def feedback_out(row: TrainingFeedback, problems: list[dict], complete: bool, source_pid: str | None):
    cells = [p["cell"] for p in problems if p["cell"] and (source_pid is None or p["pid"] == source_pid)]
    newer = [cell for cell in cells if cell["id"] > row.after_submission_id]
    new_required_pass = any(p["required"] and p["cell"] and p["cell"]["id"] > row.after_submission_id
                            and p["cell"]["passed"] for p in problems)
    passed = bool(newer) and (all(cell["passed"] for cell in newer) if source_pid else complete and new_required_pass)
    return {"id": row.id, "content": row.content, "author": row.author_name, "created_at": row.created_at,
            "read_at": row.read_at, "submission_id": row.submission_id, "source_pid": source_pid,
            "followup": {"has_new_submission": bool(newer), "passed": passed,
                         "latest_submission_id": max((cell["id"] for cell in newer), default=None)}}


async def student_tasks(session: AsyncSession, user: User, student: User, *, only_training: Training | None = None):
    stmt = select(Training).join(TrainingMember, TrainingMember.training_id == Training.id).where(
        TrainingMember.student_id == student.id, Training.status == "published", student_class_scope(student))
    if user.role == "teacher":
        stmt = stmt.where(Training.teacher_id == user.id)
    if only_training:
        stmt = stmt.where(Training.id == only_training.id)
    tasks = (await session.scalars(stmt.order_by(Training.id.desc()).limit(101))).all()
    truncated = len(tasks) > 100
    tasks = tasks[:100]
    by_task = {t.id: t for t in tasks}
    ids = list(by_task)
    problem_rows = (await session.execute(select(TrainingProblem, Problem).join(Problem, Problem.id == TrainingProblem.problem_id)
        .where(TrainingProblem.training_id.in_(ids)).order_by(TrainingProblem.position))).all() if ids else []
    pid_by_id = {p.id: p.pid for _, p in problem_rows}
    frozen = await resolve_many(session, [(p, tp.problem_version_id) for tp, p in problem_rows])
    problem_rows = [(tp, p) for (tp, _), p in zip(problem_rows, frozen, strict=True)]
    records = (await session.execute(select(Submission, JudgeJob).outerjoin(JudgeJob, JudgeJob.submission_id == Submission.id)
        .where(Submission.training_id.in_(ids), Submission.student_id == student.id).order_by(Submission.id))).all() if ids else []
    cells = {}
    source_pids = {}
    for sub, job in records:
        key = (sub.training_id, sub.problem_id)
        attempts = cells.get(key, {}).get("attempts", 0) + 1
        cells[key] = submission_cell(sub, job, by_task[sub.training_id].due_at, attempts)
        source_pids[sub.id] = pid_by_id.get(sub.problem_id)
    feedback_counts = (await session.execute(select(TrainingFeedback.training_id, func.max(TrainingFeedback.id), func.count())
        .where(TrainingFeedback.student_id == student.id, TrainingFeedback.training_id.in_(ids))
        .group_by(TrainingFeedback.training_id))).all() if ids else []
    latest_ids = [row[1] for row in feedback_counts]
    latest_feedback = {f.training_id: f for f in (await session.scalars(select(TrainingFeedback).where(TrainingFeedback.id.in_(latest_ids)))).all()} if latest_ids else {}
    counts = {tid: count for tid, _, count in feedback_counts}
    items = []
    for task in tasks:
        problems = [{"pid": p.pid, "title": p.title, "required": tp.required,
                     "cell": cells.get((task.id, p.id))} for tp, p in problem_rows if tp.training_id == task.id]
        required = [p for p in problems if p["required"]]
        complete = bool(required) and all(p["cell"] and p["cell"]["passed"] for p in required)
        feedback = latest_feedback.get(task.id)
        items.append({**training_out(task), "problems": problems, "complete": complete,
            "feedback_count": counts.get(task.id, 0),
            "latest_feedback": feedback_out(feedback, problems, complete, source_pids.get(feedback.submission_id)) if feedback else None})
    return items, truncated, source_pids


async def student_profile(session: AsyncSession, user: User, student_id: int):
    student = await accessible_student(session, user, student_id)
    items, truncated, _ = await student_tasks(session, user, student)
    return {"student": {"id": student.id, "name": student.display_name or student.username, "class_name": student.class_name},
            "items": items, "truncated": truncated, "observed_at": datetime.now(UTC),
            "written": await written_activity(session, student.id),
            "summary": {"trainings": len(items), "completed": sum(t["complete"] for t in items),
                        "remedial": sum(t["source_training_id"] is not None for t in items)}}


async def written_activity(session: AsyncSession, student_id: int):
    attempts = (await session.scalars(select(WrittenAttempt.correct).where(WrittenAttempt.student_id == student_id)
                                     .order_by(WrittenAttempt.id.desc()).limit(PROFILE_WRITTEN_ATTEMPTS))).all()
    exams = (await session.execute(select(WrittenExam.id, WrittenExam.title, WrittenExam.submitted_at).where(
        WrittenExam.student_id == student_id, WrittenExam.submitted_at.is_not(None),
    ).order_by(WrittenExam.submitted_at.desc(), WrittenExam.id.desc()).limit(PROFILE_WRITTEN_EXAMS))).all()
    scores = await summarize_exam_cards(session, [exam.id for exam in exams])
    return {
        "attempt_count": len(attempts), "full_score_count": sum(result is True for result in attempts),
        "pending_count": sum(result is None for result in attempts),
        "attempt_limit": PROFILE_WRITTEN_ATTEMPTS, "exam_limit": PROFILE_WRITTEN_EXAMS,
        "exams": [{"id": exam.id, "title": exam.title, "submitted_at": exam.submitted_at, **scores[exam.id]} for exam in exams],
    }


async def feedback_history(session: AsyncSession, user: User, training_id: int, student_id: int):
    training, student = await require_member(session, user, training_id, student_id)
    items, _, source_pids = await student_tasks(session, user, student, only_training=training)
    rows = (await session.scalars(select(TrainingFeedback).where(TrainingFeedback.training_id == training.id,
        TrainingFeedback.student_id == student.id).order_by(TrainingFeedback.id.desc()).limit(51))).all()
    task = items[0]
    return {"student": {"id": student.id, "name": student.display_name or student.username},
            "items": [feedback_out(row, task["problems"], task["complete"], source_pids.get(row.submission_id)) for row in rows[:50]],
            "truncated": len(rows) > 50, "observed_at": datetime.now(UTC)}


async def create_feedback(session: AsyncSession, user: User, training_id: int, student_id: int, payload: FeedbackCreate):
    if user.role not in ("teacher", "admin"):
        raise HTTPException(403, "只有教练可以发布个别反馈")
    training, student = await require_member(session, user, training_id, student_id, lock=True)
    content = payload.content.strip()
    if not content:
        raise HTTPException(422, "反馈内容不能为空")
    if payload.submission_id is not None:
        sub = await session.get(Submission, payload.submission_id)
        if sub is None or sub.student_id != student.id or sub.training_id != training.id:
            raise HTTPException(422, "引用提交不属于当前学生与训练")
    existing = (await session.scalars(select(TrainingFeedback).where(TrainingFeedback.training_id == training.id,
        TrainingFeedback.student_id == student.id, TrainingFeedback.request_id == str(payload.request_id)))).first()
    if existing:
        if existing.content != content or existing.submission_id != payload.submission_id or existing.author_id != user.id:
            raise HTTPException(409, "同一发送请求的内容已变化，请重新发送")
    else:
        boundary = await session.scalar(select(func.max(Submission.id)).where(Submission.training_id == training.id, Submission.student_id == student.id))
        session.add(TrainingFeedback(training_id=training.id, student_id=student.id, content=content,
            author_id=user.id, author_name=user.display_name or user.username, submission_id=payload.submission_id,
            request_id=str(payload.request_id), after_submission_id=boundary or 0))
        await session.commit()
    return await feedback_history(session, user, training_id, student_id)


async def mark_feedback_read(session: AsyncSession, user: User, training_id: int, feedback_id: int):
    if user.role != "student":
        raise HTTPException(403, "只有接收反馈的学生可以标记已查看")
    await require_member(session, user, training_id, user.id)
    row = (await session.scalars(select(TrainingFeedback).where(TrainingFeedback.id == feedback_id,
        TrainingFeedback.training_id == training_id, TrainingFeedback.student_id == user.id).with_for_update())).first()
    if row is None:
        raise HTTPException(404, "反馈不存在或无权访问")
    if row.read_at is None:
        row.read_at = datetime.now(UTC)
        await session.commit()
    return await feedback_history(session, user, training_id, user.id)


def profile_markdown(profile: dict) -> str:
    student = profile["student"]
    labels = {"AC": "正式题包通过", "SAMPLE_PASS": "样例通过", "NO_TESTS": "没有可执行测试", "UNVERIFIED": "待核对",
              "CE": "编译错误", "WA": "答案错误", "RE": "运行错误", "TLE": "超时", "MLE": "内存超限", "OLE": "输出超限",
              "pending": "判题中", "failed": "执行故障", "cancelled": "已取消"}
    lines = [f"# {student['name']} · 训练档案", f"班级：{student['class_name']}",
             f"数据时间：{profile['observed_at'].isoformat()}"]
    written = profile["written"]
    if written["attempt_count"] or written["exams"]:
        lines += ["## 初赛练习",
                  f"最近最多 {written['attempt_limit']} 次单题作答中，共作答 {written['attempt_count']} 次，"
                  f"满分 {written['full_score_count']} 次，待复核 {written['pending_count']} 次。"]
        if written["exams"]:
            lines.append(f"### 最近最多 {written['exam_limit']} 份已交模拟卷")
        for exam in written["exams"]:
            lines.append(f"- {exam['title']}：原卷 {exam['earned_score']:g} / {exam['max_score']:g} 分，"
                         f"复练答对 {exam['corrected_count']} / {exam['missed_count']} 题。")
    lines.append("编程部分汇总最近最多 100 份可访问的已发布训练。正式通过指当前题包测试通过。")
    for task in profile["items"]:
        lines += [f"## {task['title']}（训练 #{task['id']}）", "必做题已完成" if task["complete"] else "必做题尚未全部完成"]
        if task["source_training_id"]:
            lines.append(f"补练来源：训练 #{task['source_training_id']}")
        for problem in task["problems"]:
            cell = problem["cell"]
            status = (cell["verdict"] if cell["state"] == "complete" else cell["state"]) if cell else "未提交"
            status = labels.get(status, status or "待核对")
            lines.append(f"- {problem['pid']} {problem['title']}：{status}")
        feedback = task["latest_feedback"]
        if feedback:
            lines += ["### 最近个别反馈", feedback["content"], f"教练：{feedback['author']}；时间：{feedback['created_at'].isoformat()}",
                      "学生已标记查看" if feedback["read_at"] else "尚未标记查看",
                      "反馈后新提交已正式通过" if feedback["followup"]["passed"] else "反馈后有新提交" if feedback["followup"]["has_new_submission"] else "反馈后尚无新提交"]
    return "\n\n".join(lines) + "\n"
