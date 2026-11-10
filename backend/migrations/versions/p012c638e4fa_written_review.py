"""笔试逐题修订、审核记录和独立发布版本。"""

import sqlalchemy as sa
from alembic import op

from app.infra.sql_types import UTCDateTime

revision = "p012c638e4fa"
down_revision = "o901b527d3ef"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "written_candidate", sa.Column("revision", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("written_candidate", sa.Column("draft_metadata", sa.JSON(), nullable=True))
    op.add_column("written_candidate", sa.Column("reviewed_by", sa.BigInteger(), nullable=True))
    op.add_column("written_candidate", sa.Column("reviewed_at", UTCDateTime(), nullable=True))
    op.create_foreign_key(
        "fk_written_candidate_reviewer", "written_candidate", "app_user", ["reviewed_by"], ["id"]
    )
    op.create_table(
        "written_review_revision",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "candidate_id", sa.BigInteger(), sa.ForeignKey("written_candidate.id"), nullable=False
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("snapshot_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("candidate_id", "revision", name="uq_written_review_revision"),
        sa.UniqueConstraint("candidate_id", "request_id", name="uq_written_review_request"),
    )
    op.create_index(
        "ix_written_review_revision_candidate_id", "written_review_revision", ["candidate_id"]
    )
    op.create_table(
        "written_question",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "candidate_id",
            sa.BigInteger(),
            sa.ForeignKey("written_candidate.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("catalog_version", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("public_metadata", sa.JSON(), nullable=False),
    )
    op.create_table(
        "written_question_version",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "question_id", sa.BigInteger(), sa.ForeignKey("written_question.id"), nullable=False
        ),
        sa.Column(
            "batch_id", sa.String(36), sa.ForeignKey("written_import_batch.id"), nullable=False
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("review_revision", sa.Integer(), nullable=False),
        sa.Column("snapshot_metadata", sa.JSON(), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("question_id", "revision", name="uq_written_question_version"),
    )
    op.create_index(
        "ix_written_question_version_question_id", "written_question_version", ["question_id"]
    )


def downgrade():
    # 审核快照即将删除；旧版只能回到原始候选，不能保留失去依据的通过状态。
    op.execute(sa.text("""
        UPDATE written_candidate SET review_status = 'needs_review',
        search_text = JSON_UNQUOTE(JSON_EXTRACT(`record`, '$.stem')),
        question_type = JSON_UNQUOTE(JSON_EXTRACT(`record`, '$.question_type')),
        level = NULLIF(JSON_UNQUOTE(JSON_EXTRACT(`record`, '$.level')), 'null')
    """))
    op.drop_table("written_question_version")
    op.drop_table("written_question")
    op.drop_table("written_review_revision")
    op.drop_constraint("fk_written_candidate_reviewer", "written_candidate", type_="foreignkey")
    for name in ["reviewed_at", "reviewed_by", "draft_metadata", "revision"]:
        op.drop_column("written_candidate", name)
