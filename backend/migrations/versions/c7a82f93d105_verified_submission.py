"""bind tutor verification to an executed submission

Revision ID: c7a82f93d105
Revises: b354a195a638
"""

import sqlalchemy as sa
from alembic import op

revision = "c7a82f93d105"
down_revision = "b354a195a638"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("submission", "verdict", existing_type=sa.String(8), type_=sa.String(16))
    op.add_column("hint_session", sa.Column("verified_submission_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_hint_session_verified_submission", "hint_session", "submission",
        ["verified_submission_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_hint_session_verified_submission_id", "hint_session", ["verified_submission_id"])


def downgrade() -> None:
    has_wide_verdict = op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM submission WHERE length(verdict) > 8)")
    )
    if has_wide_verdict:
        raise RuntimeError("现有提交包含新版判定，无法无损缩短 verdict；请保留当前迁移版本。")
    op.drop_index("ix_hint_session_verified_submission_id", table_name="hint_session")
    op.drop_constraint("fk_hint_session_verified_submission", "hint_session", type_="foreignkey")
    op.drop_column("hint_session", "verified_submission_id")
    op.alter_column("submission", "verdict", existing_type=sa.String(16), type_=sa.String(8))
