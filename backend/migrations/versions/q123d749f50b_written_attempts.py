"""学生初赛作答回执和固定发布版本。"""

import sqlalchemy as sa
from alembic import op

from app.infra.sql_types import UTCDateTime

revision = "q123d749f50b"
down_revision = "p012c638e4fa"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "written_attempt",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("student_id", sa.BigInteger(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column(
            "question_id", sa.BigInteger(), sa.ForeignKey("written_question.id"), nullable=False
        ),
        sa.Column(
            "version_id",
            sa.String(36),
            sa.ForeignKey("written_question_version.id"),
            nullable=False,
        ),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("question_summary", sa.JSON(), nullable=False),
        sa.Column("correct", sa.Boolean(), nullable=False),
        sa.Column("earned_score", sa.Double(), nullable=False),
        sa.Column("max_score", sa.Double(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("student_id", "request_id", name="uq_written_attempt_request"),
    )
    op.create_index("ix_written_attempt_student_order", "written_attempt", ["student_id", "id"])
    op.create_index(
        "ix_written_attempt_student_question",
        "written_attempt",
        ["student_id", "question_id", "id"],
    )


def downgrade():
    op.drop_table("written_attempt")
