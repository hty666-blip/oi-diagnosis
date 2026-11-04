"""答疑消息事务回执，防止响应丢失后的重放重复追加。"""

import sqlalchemy as sa
from alembic import op

from app.infra.sql_types import UTCDateTime

revision = "n8fa0416c2de"
down_revision = "m7e93da8152b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "hint_message_receipt",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.BigInteger(),
                  sa.ForeignKey("hint_session.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("session_id", "request_hash", name="uq_hint_message_request"),
    )
    op.create_index("ix_hint_message_receipt_session_id", "hint_message_receipt", ["session_id"])


def downgrade():
    op.drop_table("hint_message_receipt")
