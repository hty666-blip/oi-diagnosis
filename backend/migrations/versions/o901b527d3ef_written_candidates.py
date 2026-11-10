"""笔试候选题包暂存，与编程题库及正式发布隔离。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import MEDIUMTEXT

from app.infra.sql_types import UTCDateTime

revision = "o901b527d3ef"
down_revision = "n8fa0416c2de"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "written_import_batch",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("expires_at", UTCDateTime(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("staged_at", UTCDateTime(), nullable=True),
        sa.UniqueConstraint("actor_id", "digest", name="uq_written_actor_digest"),
    )
    op.create_index("ix_written_import_batch_actor_id", "written_import_batch", ["actor_id"])
    op.create_index("ix_written_import_batch_status", "written_import_batch", ["status"])
    op.create_table(
        "written_candidate",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "batch_id",
            sa.String(36),
            sa.ForeignKey("written_import_batch.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(80), nullable=False),
        sa.Column("paper_id", sa.String(80), nullable=False),
        sa.Column("question_type", sa.String(24), nullable=False),
        sa.Column("level", sa.String(1), nullable=True),
        sa.Column("review_status", sa.String(24), nullable=False),
        sa.Column("search_text", sa.Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False),
        sa.Column("record", sa.JSON(), nullable=False),
        sa.Column("normalized", sa.JSON(), nullable=False),
        sa.Column("issues", sa.JSON(), nullable=False),
        sa.UniqueConstraint("batch_id", "external_id", name="uq_written_candidate"),
    )
    op.create_index("ix_written_candidate_batch_id", "written_candidate", ["batch_id"])
    op.create_table(
        "written_import_asset",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "batch_id",
            sa.String(36),
            sa.ForeignKey("written_import_batch.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.String(240), nullable=False),
        sa.Column("media_type", sa.String(40), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.UniqueConstraint("batch_id", "path", name="uq_written_asset"),
    )
    op.create_index("ix_written_import_asset_batch_id", "written_import_asset", ["batch_id"])
    op.create_table(
        "written_import_chunk",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "batch_id",
            sa.String(36),
            sa.ForeignKey("written_import_batch.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(240), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.UniqueConstraint("batch_id", "key", "position", name="uq_written_chunk"),
    )
    op.create_index("ix_written_import_chunk_batch_id", "written_import_chunk", ["batch_id"])


def downgrade():
    op.drop_table("written_import_chunk")
    op.drop_table("written_import_asset")
    op.drop_table("written_candidate")
    op.drop_table("written_import_batch")
