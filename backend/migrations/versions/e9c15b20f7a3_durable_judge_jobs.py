"""persist judge jobs with renewable fenced leases

Revision ID: e9c15b20f7a3
Revises: d8b04a19e6f2
"""

import sqlalchemy as sa
from alembic import op

revision = "e9c15b20f7a3"
down_revision = "d8b04a19e6f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "judge_job",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("submission_id", sa.BigInteger(), sa.ForeignKey("submission.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("status", sa.String(16), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("manual_retries", sa.Integer(), server_default="0", nullable=False),
        sa.Column("owner_token", sa.String(32), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')", name="ck_judge_job_status"),
        sa.CheckConstraint("attempts >= 0 AND max_attempts > 0", name="ck_judge_job_attempts"),
    )
    op.create_index("ix_judge_job_status", "judge_job", ["status"])
    op.create_index("ix_judge_job_available_at", "judge_job", ["available_at"])


def downgrade() -> None:
    op.drop_table("judge_job")
