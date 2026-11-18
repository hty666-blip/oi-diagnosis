"""初赛专项的权限范围、题面版本和班级作答矩阵。"""

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, select

from app.domain.models import (
    ClassTeacher,
    SchoolClass,
    User,
    WrittenAssignment,
    WrittenAssignmentAttempt,
    WrittenAssignmentMember,
    WrittenAssignmentQuestion,
    WrittenAttempt,
    WrittenQuestion,
    WrittenQuestionVersion,
)
from app.services.written_review import visible_query


def active_students(class_id):
    return select(User).where(
        User.class_id == class_id, User.role == "student", User.enabled.is_(True)
    )


def managed_classes(user):
    stmt = select(SchoolClass)
    if user.role == "teacher":
        stmt = stmt.join(ClassTeacher, ClassTeacher.class_id == SchoolClass.id).where(
            ClassTeacher.teacher_id == user.id
        )
    return stmt


def scoped_assignments(user):
    stmt = select(WrittenAssignment, SchoolClass).join(
        SchoolClass, SchoolClass.id == WrittenAssignment.class_id
    )
    if user.role == "student":
        return stmt.join(
            WrittenAssignmentMember, WrittenAssignmentMember.assignment_id == WrittenAssignment.id
        ).where(
            WrittenAssignmentMember.student_id == user.id,
            WrittenAssignment.class_id == user.class_id,
            SchoolClass.archived.is_(False),
        )
    if user.role == "teacher":
        return stmt.where(
            WrittenAssignment.class_id.in_(managed_classes(user).with_only_columns(SchoolClass.id))
        )
    return stmt


async def accessible_assignment(session, user, assignment_id):
    row = (
        await session.execute(scoped_assignments(user).where(WrittenAssignment.id == assignment_id))
    ).first()
    if row is None:
        raise HTTPException(404, "初赛任务不存在或无权访问")
    return row


def is_question_available(question_id):
    return visible_query().where(WrittenQuestion.id == question_id).exists()


async def assignment_version(
    session, user, assignment_id, question_id, *, submitting=False
):
    assignment, _ = await accessible_assignment(session, user, assignment_id)
    version = await session.scalar(
        select(WrittenQuestionVersion)
        .join(WrittenAssignmentQuestion, WrittenAssignmentQuestion.version_id == WrittenQuestionVersion.id)
        .where(
            WrittenAssignmentQuestion.assignment_id == assignment_id,
            WrittenAssignmentQuestion.question_id == question_id,
            WrittenQuestionVersion.question_id == question_id,
            is_question_available(question_id),
        )
    )
    if version is None:
        if submitting:
            raise HTTPException(409, "该题不在本专项中或已下架，请返回任务列表")
        raise HTTPException(404, "任务题目不存在或已下架")
    if submitting and assignment.due_at <= datetime.now(UTC):
        raise HTTPException(409, "初赛任务已截止")
    return version


def assignment_summary(assignment, group):
    return {
        "id": assignment.id,
        "class_id": group.id,
        "class_name": group.name,
        "title": assignment.title,
        "due_at": assignment.due_at,
        "is_closed": group.archived or assignment.due_at <= datetime.now(UTC),
    }


async def assignment_detail(session, user, assignment, group):
    available = is_question_available(WrittenAssignmentQuestion.question_id).correlate(
        WrittenAssignmentQuestion
    )
    questions = (
        await session.execute(
            select(WrittenAssignmentQuestion, available)
            .where(WrittenAssignmentQuestion.assignment_id == assignment.id)
            .order_by(WrittenAssignmentQuestion.position)
        )
    ).all()
    stmt = (
        active_students(group.id)
        .join(WrittenAssignmentMember, WrittenAssignmentMember.student_id == User.id)
        .where(WrittenAssignmentMember.assignment_id == assignment.id)
        .order_by(User.id)
    )
    if user.role == "student":
        stmt = stmt.where(User.id == user.id)
    students = (await session.scalars(stmt)).all()
    latest = (
        select(func.max(WrittenAttempt.id))
        .join(WrittenAssignmentAttempt, WrittenAssignmentAttempt.attempt_id == WrittenAttempt.id)
        .where(
            WrittenAssignmentAttempt.assignment_id == assignment.id,
            WrittenAttempt.student_id.in_([student.id for student in students]),
        )
        .group_by(WrittenAttempt.student_id, WrittenAttempt.question_id)
    )
    cells = {}
    if students:
        records = await session.execute(
            select(WrittenAttempt.id, WrittenAttempt.student_id, WrittenAttempt.question_id,
                   WrittenAttempt.correct, WrittenAttempt.earned_score)
            .where(WrittenAttempt.id.in_(latest))
        )
        for ident, student_id, question_id, correct, score in records:
            cells[(student_id, question_id)] = {"id": ident, "correct": correct, "earned_score": score}

    rows = []
    for student in students:
        marks = [cells.get((student.id, question.question_id)) for question, _ in questions]
        rows.append({
            "student_id": student.id,
            "name": student.display_name or student.username,
            "cells": marks,
            "submitted_count": sum(mark is not None for mark in marks),
            "correct_count": sum(mark is not None and mark["correct"] is True for mark in marks),
        })
    return {
        **assignment_summary(assignment, group),
        "questions": [
            {
                **question.summary,
                "stem": question.summary["stem"] if is_available else f"题目 #{question.question_id}",
                "is_available": is_available,
            }
            for question, is_available in questions
        ],
        "rows": rows,
        "summary": {
            "students": len(rows),
            "submitted_all": sum(row["submitted_count"] == len(questions) for row in rows),
            "correct_all": sum(row["correct_count"] == len(questions) for row in rows),
        },
    }
