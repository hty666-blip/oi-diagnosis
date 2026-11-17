"""审核发布初赛知识条目并保留历史版本。"""

import sqlalchemy as sa
from alembic import op

from app.infra.sql_types import UTCDateTime

revision = "s345f96b072d"
down_revision = "r234e85a061c"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "written_knowledge_entry",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "batch_id", sa.String(36), sa.ForeignKey("written_import_batch.id"), nullable=False
        ),
        sa.Column("point_id", sa.String(80), nullable=False),
        sa.Column("point_name", sa.String(160), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("source", sa.String(500), nullable=False),
        sa.Column("edit_version", sa.Integer(), nullable=False),
        sa.Column("published_revision", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("editor_id", sa.BigInteger(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.UniqueConstraint("batch_id", "point_id", name="uq_written_knowledge_point"),
    )
    op.create_table(
        "written_knowledge_version",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "entry_id", sa.BigInteger(), sa.ForeignKey("written_knowledge_entry.id"), nullable=False
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("source", sa.String(500), nullable=False),
        sa.Column("review_note", sa.String(400), nullable=False),
        sa.Column("reviewer_id", sa.BigInteger(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("created_at", UTCDateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("entry_id", "revision", name="uq_written_knowledge_revision"),
    )


def downgrade():
    op.drop_table("written_knowledge_version")
    op.drop_table("written_knowledge_entry")
