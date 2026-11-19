"""support transaction-scoped submission serialization on MySQL

Revision ID: g1e37d42b9c5
Revises: f0d26c31a8b4
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import MEDIUMTEXT

revision = "g1e37d42b9c5"
down_revision = "f0d26c31a8b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "submission_lock",
        sa.Column("lock_key", sa.BigInteger(), primary_key=True, autoincrement=False),
    )
    if op.get_bind().dialect.name == "mysql":
        # 接口允许十万字符代码，普通 TEXT 的 64 KiB 无法保存合法长提交。
        for table, column in (("submission", "code"), ("problem", "statement_md")):
            op.alter_column(table, column, existing_type=sa.Text(), type_=MEDIUMTEXT(),
                            existing_nullable=False)


def downgrade() -> None:
    if op.get_bind().dialect.name == "mysql":
        for table, column in (("submission", "code"), ("problem", "statement_md")):
            if op.get_bind().scalar(sa.text(
                f"SELECT EXISTS (SELECT 1 FROM {table} WHERE OCTET_LENGTH({column}) > 65535)"
            )):
                raise RuntimeError("cannot downgrade while long source text exists")
        for table, column in (("submission", "code"), ("problem", "statement_md")):
            op.alter_column(table, column, existing_type=MEDIUMTEXT(), type_=sa.Text(),
                            existing_nullable=False)
    op.drop_table("submission_lock")
