"""一次性学生注册邀请码。

Revision ID: x890e41a2c7d
Revises: w789d30f4b60
"""

import sqlalchemy as sa
from alembic import op

revision = "x890e41a2c7d"
down_revision = "w789d30f4b60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "student_invite",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("class_id", sa.BigInteger(), sa.ForeignKey("school_class.id"), nullable=False),
        sa.Column("code_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("created_by_id", sa.BigInteger(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("used_by_id", sa.BigInteger(), sa.ForeignKey("app_user.id"), nullable=True),
    )
    op.create_index("ix_student_invite_class_id", "student_invite", ["class_id"])


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM student_invite)")):
        raise RuntimeError("已有邀请码签发记录，请使用独立环境恢复旧版本备份")
    op.drop_table("student_invite")
