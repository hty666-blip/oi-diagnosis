"""固定初赛专项的名单、审核题目版本及专项作答关系。"""

import sqlalchemy as sa
from alembic import op

from app.infra.sql_types import UTCDateTime

revision = "t456a07c183e"
down_revision = "s345f96b072d"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "written_assignment",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("class_id", sa.BigInteger(), sa.ForeignKey("school_class.id"), nullable=False),
        sa.Column("teacher_id", sa.BigInteger(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("due_at", UTCDateTime(), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("created_at", UTCDateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("teacher_id", "request_id", name="uq_written_assignment_request"),
    )
    op.create_index("ix_written_assignment_class", "written_assignment", ["class_id", "id"])
    op.create_table(
        "written_assignment_question",
        sa.Column(
            "assignment_id", sa.BigInteger(), sa.ForeignKey("written_assignment.id"), primary_key=True
        ),
        sa.Column(
            "question_id", sa.BigInteger(), sa.ForeignKey("written_question.id"), primary_key=True
        ),
        sa.Column(
            "version_id", sa.String(36), sa.ForeignKey("written_question_version.id"), nullable=False
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
    )
    op.create_table(
        "written_assignment_member",
        sa.Column(
            "assignment_id", sa.BigInteger(), sa.ForeignKey("written_assignment.id"), primary_key=True
        ),
        sa.Column("student_id", sa.BigInteger(), sa.ForeignKey("app_user.id"), primary_key=True),
    )
    op.create_table(
        "written_assignment_attempt",
        sa.Column(
            "attempt_id", sa.BigInteger(), sa.ForeignKey("written_attempt.id"), primary_key=True
        ),
        sa.Column(
            "assignment_id", sa.BigInteger(), sa.ForeignKey("written_assignment.id"), nullable=False
        ),
    )
    op.create_index(
        "ix_written_assignment_attempt_assignment_id", "written_assignment_attempt", ["assignment_id"]
    )


def downgrade():
    op.drop_table("written_assignment_attempt")
    op.drop_table("written_assignment_member")
    op.drop_table("written_assignment_question")
    op.drop_table("written_assignment")
