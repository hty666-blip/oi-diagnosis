"""班级简答题待复核状态和人工确认凭据。"""

import sqlalchemy as sa
from alembic import op

from app.infra.sql_types import UTCDateTime

revision = "v678c29e3a5f"
down_revision = "u567b18d294f"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("written_attempt", "correct", existing_type=sa.Boolean(), nullable=True)
    op.alter_column("written_attempt", "earned_score", existing_type=sa.Double(), nullable=True)
    op.create_table(
        "written_short_grade",
        sa.Column("attempt_id", sa.BigInteger(), sa.ForeignKey("written_attempt.id"), primary_key=True),
        sa.Column("teacher_id", sa.BigInteger(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("points", sa.JSON(), nullable=False),
        sa.Column("feedback", sa.Text(), nullable=False),
        sa.Column("created_at", UTCDateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "written_short_draft",
        sa.Column("attempt_id", sa.BigInteger(), sa.ForeignKey("written_attempt.id"), primary_key=True),
        sa.Column("claim", sa.String(36), nullable=False),
        sa.Column("lease_until", UTCDateTime(), nullable=False),
        sa.Column("suggestion", sa.JSON(none_as_null=True), nullable=True),
    )


def downgrade():
    active = op.get_bind().scalar(sa.text(
        "SELECT EXISTS(SELECT 1 FROM written_short_grade) "
        "OR EXISTS(SELECT 1 FROM written_attempt WHERE correct IS NULL OR earned_score IS NULL)"
    ))
    if active:
        raise RuntimeError("简答作答已存在，降级会丢失评分，请在独立环境恢复旧版备份")
    op.drop_table("written_short_draft")
    op.drop_table("written_short_grade")
    op.alter_column("written_attempt", "earned_score", existing_type=sa.Double(), nullable=False)
    op.alter_column("written_attempt", "correct", existing_type=sa.Boolean(), nullable=False)
