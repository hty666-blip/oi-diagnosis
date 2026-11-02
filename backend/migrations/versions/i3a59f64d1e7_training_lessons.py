"""训练讲评版本与发布记录。"""
import sqlalchemy as sa
from alembic import op

from app.infra.sql_types import UTCDateTime

revision = "i3a59f64d1e7"
down_revision = "h2f48e53c0d6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("training_lesson_revision",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("training_id", sa.BigInteger(), sa.ForeignKey("training.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("basis_digest", sa.String(64), nullable=False),
        sa.Column("basis", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("created_at", UTCDateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("training_id", "revision", name="uq_training_lesson_revision"))
    op.create_index("ix_training_lesson_revision_training_id", "training_lesson_revision", ["training_id"])
    op.create_table("training_lesson_publication",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("training_id", sa.BigInteger(), sa.ForeignKey("training.id"), nullable=False),
        sa.Column("lesson_revision_id", sa.BigInteger(), sa.ForeignKey("training_lesson_revision.id"), nullable=False, unique=True),
        sa.Column("published_by", sa.BigInteger(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("created_at", UTCDateTime(), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_training_lesson_publication_training_id", "training_lesson_publication", ["training_id"])


def downgrade():
    op.drop_table("training_lesson_publication")
    op.drop_table("training_lesson_revision")
