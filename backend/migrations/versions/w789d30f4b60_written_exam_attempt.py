"""将整卷错题复练关联到原答卷。"""

import sqlalchemy as sa
from alembic import op

revision = "w789d30f4b60"
down_revision = "v678c29e3a5f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "written_exam_attempt",
        sa.Column("attempt_id", sa.BigInteger(), sa.ForeignKey("written_attempt.id"), primary_key=True),
        sa.Column("exam_id", sa.BigInteger(), sa.ForeignKey("written_exam.id"), nullable=False),
    )
    op.create_index("ix_written_exam_attempt_exam_id", "written_exam_attempt", ["exam_id"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM written_exam_attempt)")):
        raise RuntimeError("已有模拟复练关联，请使用独立环境恢复旧版本备份")
    op.drop_table("written_exam_attempt")
