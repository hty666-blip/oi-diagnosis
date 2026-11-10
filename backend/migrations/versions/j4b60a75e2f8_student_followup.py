"""定向补练、个别反馈与查看记录。"""
import sqlalchemy as sa
from alembic import op

from app.infra.sql_types import UTCDateTime

revision = "j4b60a75e2f8"
down_revision = "i3a59f64d1e7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("training", sa.Column("student_ids", sa.JSON(), nullable=True))
    op.add_column("training", sa.Column("source_training_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_training_source_training_id", "training", ["source_training_id"])
    op.create_foreign_key("fk_training_source", "training", "training", ["source_training_id"], ["id"])
    op.create_table("training_feedback",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("training_id", sa.BigInteger(), sa.ForeignKey("training.id"), nullable=False),
        sa.Column("student_id", sa.BigInteger(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("author_id", sa.BigInteger(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("author_name", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("submission_id", sa.BigInteger(), sa.ForeignKey("submission.id"), nullable=True),
        sa.Column("after_submission_id", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("read_at", UTCDateTime(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("training_id", "student_id", "request_id", name="uq_training_feedback_request"))
    op.create_index("ix_training_feedback_training_id", "training_feedback", ["training_id"])
    op.create_index("ix_training_feedback_student_id", "training_feedback", ["student_id"])


def downgrade():
    op.drop_table("training_feedback")
    op.drop_constraint("fk_training_source", "training", type_="foreignkey")
    op.drop_index("ix_training_source_training_id", table_name="training")
    op.drop_column("training", "source_training_id")
    op.drop_column("training", "student_ids")
