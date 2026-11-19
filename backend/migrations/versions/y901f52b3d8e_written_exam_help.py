"""保存模拟答卷的错题追问和诊断交接。"""

import sqlalchemy as sa
from alembic import op

from app.infra.sql_types import UTCDateTime

revision = "y901f52b3d8e"
down_revision = "x890e41a2c7d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "written_exam_help_turn",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("exam_id", sa.BigInteger(), nullable=False),
        sa.Column("question_id", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("claim_token", sa.String(36), nullable=False),
        sa.Column("lease_until", UTCDateTime(), nullable=False),
        sa.Column("result", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["exam_id", "question_id"], ["written_exam_item.exam_id", "written_exam_item.question_id"],
                                name="fk_written_exam_help_item"),
        sa.UniqueConstraint("exam_id", "question_id", "request_id", name="uq_written_exam_help_request"),
    )
    op.create_index("ix_written_exam_help_order", "written_exam_help_turn", ["exam_id", "question_id", "id"])


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM written_exam_help_turn)")):
        raise RuntimeError("已有模拟答卷答疑，请使用独立环境恢复旧版本备份")
    op.drop_table("written_exam_help_turn")
