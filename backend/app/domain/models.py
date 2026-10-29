"""领域模型（M0 只落 4 张核心表，其余按里程碑增量迁移）。

设计要点：
- evidence 是一等公民（架构 §5）：有 type / payload / digest / reproducible
- diagnosis 分开存 mechanism(M) 与 nature(P)，对应"双轴错因"
- 所有时间用服务器时间，避免时区漂移
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Problem(Base):
    __tablename__ = "problem"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pid: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # P1046 / PTV-0001
    title: Mapped[str] = mapped_column(String(200), default="")
    statement_md: Mapped[str] = mapped_column(Text, default="")
    samples: Mapped[list] = mapped_column(JSONB, default=list)
    difficulty: Mapped[str] = mapped_column(String(32), default="")
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    knowledge_points: Mapped[list] = mapped_column(JSONB, default=list)

    # A/B/C/D：决定这道题能支撑到哪一级诊断（架构 §9.6）
    package_level: Mapped[str] = mapped_column(String(2), default="C")
    has_testdata: Mapped[bool] = mapped_column(Boolean, default=False)

    source: Mapped[str] = mapped_column(String(32), default="")
    source_url: Mapped[str] = mapped_column(String(500), default="")
    license_note: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    submissions: Mapped[list[Submission]] = relationship(back_populates="problem")


class Submission(Base):
    __tablename__ = "submission"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    problem_id: Mapped[int] = mapped_column(ForeignKey("problem.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[int] = mapped_column(BigInteger, index=True, default=0)  # user 表后续补
    parent_submission_id: Mapped[int | None] = mapped_column(
        ForeignKey("submission.id", ondelete="SET NULL"), nullable=True, index=True
    )

    code: Mapped[str] = mapped_column(Text, default="")
    code_hash: Mapped[str] = mapped_column(String(64), index=True, default="")
    lang: Mapped[str] = mapped_column(String(16), default="cpp17")

    verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_kb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tests_passed: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # 状态机（架构 §5.3）
    stage: Mapped[str] = mapped_column(String(24), default="RECEIVED", index=True)
    pipeline_ver: Mapped[str] = mapped_column(String(32), default="v1")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    problem: Mapped[Problem] = relationship(back_populates="submissions")
    evidence: Mapped[list[Evidence]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )
    diagnosis: Mapped[Diagnosis | None] = relationship(
        back_populates="submission", cascade="all, delete-orphan", uselist=False
    )


class JudgeJob(Base):
    """与提交同事务保存的判题任务；租约令牌隔离失效执行者。"""

    __tablename__ = "judge_job"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')", name="ck_judge_job_status"),
        CheckConstraint("attempts >= 0 AND max_attempts > 0", name="ck_judge_job_attempts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submission.id", ondelete="CASCADE"), unique=True
    )
    status: Mapped[str] = mapped_column(String(16), default="queued", server_default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    manual_retries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    owner_token: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submission.id", ondelete="CASCADE"), index=True
    )
    # compile_log | run_trace | sanitizer | counterexample | timing_curve | static_alert
    type: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    reproducible: Mapped[bool] = mapped_column(Boolean, default=True)
    digest: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    submission: Mapped[Submission] = relationship(back_populates="evidence")


class Diagnosis(Base):
    __tablename__ = "diagnosis"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submission.id", ondelete="CASCADE"), unique=True
    )

    # 机制轴：[{"code":"M01","conf":0.91,"evidence_ids":[1,2]}]
    mechanism: Mapped[list] = mapped_column(JSONB, default=list)
    # 认知轴：[{"code":"C4","conf":0.62,"basis":"inferred"}]
    cognition: Mapped[list] = mapped_column(JSONB, default=list)
    # 性质轴（错因体系 v2）：[{"p":"P0","line":3,"col":12,"code":"P0-FULLWIDTH"}]
    nature: Mapped[list] = mapped_column(JSONB, default=list)

    narrative: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft/confirmed/needs_human/rejected
    degraded: Mapped[list] = mapped_column(JSONB, default=list)
    reviewer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 自动重诊断和人工审定均递增，用于拒绝基于旧内容的写入。
    review_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    model_meta: Mapped[dict] = mapped_column(JSONB, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    submission: Mapped[Submission] = relationship(back_populates="diagnosis")


class DiagnosisReview(Base):
    """追加式教师审计；数据库触发器禁止覆盖或删除已保存记录。"""

    __tablename__ = "diagnosis_review"
    __table_args__ = (
        UniqueConstraint("diagnosis_id", "revision", name="uq_diagnosis_review_version"),
        CheckConstraint("decision IN ('confirm', 'revise', 'return')", name="ck_review_decision"),
        CheckConstraint("revision > 0", name="ck_review_revision_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    diagnosis_id: Mapped[int] = mapped_column(
        ForeignKey("diagnosis.id", ondelete="RESTRICT"), index=True
    )
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submission.id", ondelete="RESTRICT"), index=True
    )
    actor_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="RESTRICT"))
    actor_name: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(16))
    narrative: Mapped[str] = mapped_column(Text)
    evidence_ids: Mapped[list] = mapped_column(JSONB, default=list)
    before: Mapped[dict] = mapped_column(JSONB)
    after: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HintSession(Base):
    """一次答疑会话：从某次提交出发，按提示阶梯逐级推进。"""

    __tablename__ = "hint_session"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submission.id", ondelete="CASCADE"), index=True)
    # 最近一次实际核验的修改后提交；通过范围是否足够由提交的 tests_passed 决定。
    verified_submission_id: Mapped[int | None] = mapped_column(
        ForeignKey("submission.id", ondelete="SET NULL"), nullable=True, index=True
    )
    student_id: Mapped[int] = mapped_column(BigInteger, index=True, default=0)

    level: Mapped[int] = mapped_column(Integer, default=0)       # 当前提示层级 L0..L4
    status: Mapped[str] = mapped_column(String(16), default="open")  # open/resolved/escalated/blocked
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    cheat_flags: Mapped[int] = mapped_column(Integer, default=0)  # 索要答案的次数
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    turns: Mapped[list[HintTurn]] = relationship(
        back_populates="session", cascade="all, delete-orphan")


class HintTurn(Base):
    __tablename__ = "hint_turn"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("hint_session.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(8), default="tutor")   # tutor | student
    level: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[HintSession] = relationship(back_populates="turns")


class Mastery(Base):
    """知识点掌握度（BKT），带不确定度，便于"宁可不说"。"""

    __tablename__ = "mastery"

    student_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    node: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[float] = mapped_column(default=0.3)
    uncertainty: Mapped[float] = mapped_column(default=0.5)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class User(Base):
    """账号：学生 / 教练 / 管理员。密码用 pbkdf2 存哈希，不落明文。"""

    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(64), default="")
    role: Mapped[str] = mapped_column(String(16), default="student")  # student|teacher|admin
    password_hash: Mapped[str] = mapped_column(String(256), default="")
    class_name: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
