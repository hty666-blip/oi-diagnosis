"""管理员账号控制、稳定班级归属及操作审计。"""
import sqlalchemy as sa
from alembic import op

from app.infra.sql_types import UTCDateTime

revision = "k5c71b86f309"
down_revision = "j4b60a75e2f8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("school_class",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"))
    for table in ("app_user", "training"):
        op.add_column(table, sa.Column("class_id", sa.BigInteger(), nullable=True))
        op.create_foreign_key(f"fk_{table}_class", table, "school_class", ["class_id"], ["id"])
        op.create_index(f"ix_{table}_class_id", table, ["class_id"])
    op.add_column("app_user", sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"))
    op.add_column("app_user", sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("app_user", sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default="0"))
    op.add_column("training", sa.Column("original_teacher_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_training_original_teacher", "training", "app_user", ["original_teacher_id"], ["id"])
    op.create_table("class_teacher",
        sa.Column("class_id", sa.BigInteger(), sa.ForeignKey("school_class.id"), primary_key=True),
        sa.Column("teacher_id", sa.BigInteger(), sa.ForeignKey("app_user.id"), primary_key=True))
    op.create_table("admin_audit",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target", sa.String(100), nullable=False),
        sa.Column("before", sa.JSON(), nullable=False),
        sa.Column("after", sa.JSON(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False, server_default=sa.func.now()))
    # 一次性回填既有非空班级；此后管理操作只使用稳定编号。
    conn = op.get_bind()
    names = conn.execute(sa.text("SELECT class_name FROM app_user UNION SELECT class_name FROM training")).scalars().all()
    for name in names:
        if not name or not name.strip():
            continue
        conn.execute(sa.text("INSERT INTO school_class (name) VALUES (:name)"), {"name": name})
        class_id = conn.execute(sa.text("SELECT id FROM school_class WHERE name=:name"), {"name": name}).scalar_one()
        for table in ("app_user", "training"):
            conn.execute(sa.text(f"UPDATE {table} SET class_id=:cid WHERE class_name=:name"), {"cid": class_id, "name": name})
    conn.execute(sa.text("INSERT INTO class_teacher (class_id, teacher_id) SELECT class_id, id FROM app_user WHERE role='teacher' AND class_id IS NOT NULL"))
    conn.execute(sa.text("UPDATE training SET original_teacher_id=teacher_id"))


def downgrade():
    op.drop_table("admin_audit")
    op.drop_table("class_teacher")
    op.drop_constraint("fk_training_original_teacher", "training", type_="foreignkey")
    op.drop_column("training", "original_teacher_id")
    for column in ("enabled", "token_version", "must_change_password"):
        op.drop_column("app_user", column)
    for table in ("training", "app_user"):
        op.drop_constraint(f"fk_{table}_class", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_class_id", table_name=table)
        op.drop_column(table, "class_id")
    op.drop_table("school_class")
