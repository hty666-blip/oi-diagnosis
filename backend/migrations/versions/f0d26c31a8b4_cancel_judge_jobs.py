"""allow a cancelled judge job terminal state

Revision ID: f0d26c31a8b4
Revises: e9c15b20f7a3
"""

from alembic import op

revision = "f0d26c31a8b4"
down_revision = "e9c15b20f7a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_judge_job_status", "judge_job", type_="check")
    op.create_check_constraint("ck_judge_job_status", "judge_job",
                               "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')")


def downgrade() -> None:
    # 旧版本无法理解 cancelled，明确拒绝有取消数据时静默降级，避免篡改终态。
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM judge_job WHERE status = 'cancelled') THEN
                RAISE EXCEPTION 'cannot downgrade while cancelled judge jobs exist';
            END IF;
        END $$
    """)
    op.drop_constraint("ck_judge_job_status", "judge_job", type_="check")
    op.create_check_constraint("ck_judge_job_status", "judge_job",
                               "status IN ('queued', 'running', 'succeeded', 'failed')")
