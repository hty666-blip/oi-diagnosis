"""持久化限时整卷模拟及固定版本的答题卡。"""

import sqlalchemy as sa
from alembic import op

from app.infra.sql_types import UTCDateTime

revision = "u567b18d294f"
down_revision = "t456a07c183e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "written_exam",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("student_id", sa.BigInteger(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("written_import_batch.id"), nullable=False),
        sa.Column("paper_id", sa.String(80), nullable=False),
        sa.Column("title", sa.String(180), nullable=False),
        sa.Column("started_at", UTCDateTime(), nullable=False),
        sa.Column("deadline", UTCDateTime(), nullable=False),
        sa.Column("submitted_at", UTCDateTime(), nullable=True),
        sa.UniqueConstraint("student_id", "batch_id", "paper_id", name="uq_written_exam_paper"),
    )
    op.create_index("ix_written_exam_student_order", "written_exam", ["student_id", "id"])
    op.create_table(
        "written_exam_item",
        sa.Column("exam_id", sa.BigInteger(), sa.ForeignKey("written_exam.id"), primary_key=True),
        sa.Column("question_id", sa.BigInteger(), sa.ForeignKey("written_question.id"), primary_key=True),
        sa.Column("version_id", sa.String(36), sa.ForeignKey("written_question_version.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(32), nullable=False),
        sa.Column("max_score", sa.Double(), nullable=False),
        sa.Column("response", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("earned_score", sa.Double(), nullable=True),
        sa.UniqueConstraint("exam_id", "position", name="uq_written_exam_position"),
    )


def downgrade():
    op.drop_table("written_exam_item")
    op.drop_table("written_exam")
