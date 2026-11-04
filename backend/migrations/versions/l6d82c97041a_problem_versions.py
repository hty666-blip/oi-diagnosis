"""题库版本、发布状态、导入预览和训练/提交快照。"""
from dataclasses import asdict
from types import SimpleNamespace

import sqlalchemy as sa
from alembic import op

from app.capabilities.sandbox import SandboxError
from app.capabilities.test_suite import load_package, load_suite
from app.infra.sql_types import UTCDateTime
from app.services.problem_versions import FIELDS, version_digest

revision = "l6d82c97041a"
down_revision = "k5c71b86f309"
branch_labels = None
depends_on = None


def upgrade():
    for name, kind, default in (("is_active", sa.Boolean(), "1"), ("revision", sa.Integer(), "0"), ("catalog_version", sa.Integer(), "0")):
        op.add_column("problem", sa.Column(name, kind, nullable=False, server_default=default))
    versions = op.create_table("problem_version",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("problem_id", sa.BigInteger(), sa.ForeignKey("problem.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("suite", sa.JSON(), nullable=True),
        sa.Column("reviewer", sa.String(100), nullable=False, server_default=""),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("problem_id", "revision", name="uq_problem_revision"))
    op.create_index("ix_problem_version_problem_id", "problem_version", ["problem_id"])
    for table in ("submission", "training_problem"):
        op.add_column(table, sa.Column("problem_version_id", sa.BigInteger(), nullable=True))
        op.create_foreign_key(f"fk_{table}_problem_version", table, "problem_version", ["problem_version_id"], ["id"])
    op.create_index("ix_submission_problem_version_id", "submission", ["problem_version_id"])
    op.create_table("problem_import_draft",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("records", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("expires_at", UTCDateTime(), nullable=False))
    op.create_index("ix_problem_import_draft_actor_id", "problem_import_draft", ["actor_id"])
    conn = op.get_bind()
    problems = sa.Table("problem", sa.MetaData(), autoload_with=conn)
    for item in conn.execute(sa.select(problems)).mappings().all():
        data = {key: item[key] for key in FIELDS}
        try:
            suite = asdict(load_suite(SimpleNamespace(**data)))
            reviewer = str(load_package(data["pid"])[0].get("reviewer", "")) if data["has_testdata"] and data["package_level"] in ("A", "B") else ""
        except SandboxError:
            # 无法还原的测试绝不能在重试时偷偷改用最新题包。
            suite = None
            reviewer = ""
        result = conn.execute(versions.insert().values(problem_id=item["id"], revision=1,
            data=data, suite=suite, reviewer=reviewer, digest=version_digest(data, suite, reviewer)))
        vid = result.inserted_primary_key[0]
        conn.execute(problems.update().where(problems.c.id == item["id"]).values(revision=1))
        for table in ("submission", "training_problem"):
            conn.execute(sa.text(f"UPDATE {table} SET problem_version_id=:vid WHERE problem_id=:pid"), {"vid": vid, "pid": item["id"]})


def downgrade():
    op.drop_table("problem_import_draft")
    for table in ("training_problem", "submission"):
        op.drop_constraint(f"fk_{table}_problem_version", table, type_="foreignkey")
        if table == "submission":
            op.drop_index("ix_submission_problem_version_id", table_name="submission")
        op.drop_column(table, "problem_version_id")
    op.drop_table("problem_version")
    for name in ("catalog_version", "revision", "is_active"):
        op.drop_column("problem", name)
