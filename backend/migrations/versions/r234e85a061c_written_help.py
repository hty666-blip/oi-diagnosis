"""按作答保存初赛诊断交接和答疑追问。"""

import sqlalchemy as sa
from alembic import op

from app.infra.sql_types import UTCDateTime

revision = "r234e85a061c"
down_revision = "q123d749f50b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "written_help_turn",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "attempt_id", sa.BigInteger(), sa.ForeignKey("written_attempt.id"), nullable=False
        ),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("claim_token", sa.String(36), nullable=False),
        sa.Column("lease_until", UTCDateTime(), nullable=False),
        sa.Column("result", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("attempt_id", "request_id", name="uq_written_help_request"),
    )
    op.create_index("ix_written_help_order", "written_help_turn", ["attempt_id", "id"])


def downgrade():
    op.drop_table("written_help_turn")
