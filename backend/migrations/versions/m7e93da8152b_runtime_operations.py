"""工作器心跳、模型配置检查与加密备份任务。"""
import sqlalchemy as sa
from alembic import op

from app.infra.sql_types import UTCDateTime

revision = "m7e93da8152b"
down_revision = "l6d82c97041a"
branch_labels = None
depends_on = None


def date(name, *, nullable=False, default=False):
    return sa.Column(name, UTCDateTime(), nullable=nullable, **({"server_default": sa.func.now()} if default else {}))


def upgrade():
    op.create_table("worker_heartbeat", sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(24), nullable=False), sa.Column("state", sa.String(16), nullable=False),
        sa.Column("current_job", sa.String(36), nullable=True), date("started_at", default=True), date("seen_at", default=True))
    op.create_table("ai_configuration", sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=False), sa.Column("model", sa.String(100), nullable=False),
        sa.Column("encrypted_key", sa.Text(), nullable=False), date("checked_at", nullable=True))
    op.create_table("ai_configuration_check", sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False), sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("model", sa.String(100), nullable=False), sa.Column("encrypted_key", sa.Text(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False), sa.Column("error", sa.String(64), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True), date("expires_at"), date("created_at", default=True))
    op.create_table("ai_call_event", sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("feature", sa.String(24), nullable=False), sa.Column("model", sa.String(100), nullable=False),
        sa.Column("called", sa.Boolean(), nullable=False), sa.Column("used_model", sa.Boolean(), nullable=False),
        sa.Column("error", sa.String(64), nullable=False), sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=True), sa.Column("completion_tokens", sa.BigInteger(), nullable=True),
        date("created_at", default=True))
    op.create_index("ix_ai_call_event_created_at", "ai_call_event", ["created_at"])
    op.create_table("backup_artifact", sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("file_name", sa.String(100), nullable=False), sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False), sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column("revision", sa.String(32), nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("app_user.id"), nullable=False),
        date("created_at", default=True), date("verified_at", nullable=True))
    op.create_table("maintenance_task", sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("request_id", sa.String(36), nullable=False, unique=True), sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("artifact_id", sa.String(36), sa.ForeignKey("backup_artifact.id"), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("actor_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("owner", sa.String(36), nullable=True), date("lease_until", nullable=True),
        sa.Column("error", sa.String(64), nullable=False), sa.Column("result", sa.JSON(), nullable=False),
        date("created_at", default=True), date("finished_at", nullable=True))


def downgrade():
    for table in ("maintenance_task", "backup_artifact", "ai_call_event", "ai_configuration_check", "ai_configuration", "worker_heartbeat"):
        op.drop_table(table)
