"""训练任务、发布名单及提交绑定。"""
import sqlalchemy as sa
from alembic import op

from app.infra.sql_types import UTCDateTime

revision = "h2f48e53c0d6"
down_revision = "g1e37d42b9c5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("training",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("teacher_id", sa.BigInteger(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("class_name", sa.String(64), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("objective", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("due_at", UTCDateTime(), nullable=False),
        sa.Column("published_at", UTCDateTime(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('draft', 'published')", name="ck_training_status"))
    op.create_index("ix_training_teacher_id", "training", ["teacher_id"])
    op.create_table("training_problem",
        sa.Column("training_id", sa.BigInteger(), sa.ForeignKey("training.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("problem_id", sa.BigInteger(), sa.ForeignKey("problem.id"), primary_key=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False))
    op.create_table("training_member",
        sa.Column("training_id", sa.BigInteger(), sa.ForeignKey("training.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("student_id", sa.BigInteger(), sa.ForeignKey("app_user.id"), primary_key=True))
    op.add_column("submission", sa.Column("training_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_submission_training_id", "submission", ["training_id"])
    op.create_foreign_key("fk_submission_training", "submission", "training", ["training_id"], ["id"])


def downgrade():
    op.drop_constraint("fk_submission_training", "submission", type_="foreignkey")
    op.drop_index("ix_submission_training_id", table_name="submission")
    op.drop_column("submission", "training_id")
    op.drop_table("training_member")
    op.drop_table("training_problem")
    op.drop_table("training")
