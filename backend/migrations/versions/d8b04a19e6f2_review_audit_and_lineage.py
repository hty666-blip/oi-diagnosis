"""append teacher review audits and submission lineage

Revision ID: d8b04a19e6f2
Revises: c7a82f93d105
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d8b04a19e6f2"
down_revision = "c7a82f93d105"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("submission", sa.Column("parent_submission_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_submission_parent", "submission", "submission", ["parent_submission_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_submission_parent_submission_id", "submission", ["parent_submission_id"])
    op.add_column("diagnosis", sa.Column("review_revision", sa.Integer(), server_default="0", nullable=False))
    op.add_column("diagnosis", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "diagnosis_review",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("diagnosis_id", sa.BigInteger(), sa.ForeignKey("diagnosis.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("submission_id", sa.BigInteger(), sa.ForeignKey("submission.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_name", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("narrative", sa.Text(), nullable=False),
        sa.Column("evidence_ids", postgresql.JSONB(), nullable=False),
        sa.Column("before", postgresql.JSONB(), nullable=False),
        sa.Column("after", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("diagnosis_id", "revision", name="uq_diagnosis_review_version"),
        sa.CheckConstraint("decision IN ('confirm', 'revise', 'return')", name="ck_review_decision"),
        sa.CheckConstraint("revision > 0", name="ck_review_revision_positive"),
    )
    op.create_index("ix_diagnosis_review_diagnosis_id", "diagnosis_review", ["diagnosis_id"])
    op.create_index("ix_diagnosis_review_submission_id", "diagnosis_review", ["submission_id"])
    op.execute("""
        CREATE FUNCTION reject_diagnosis_review_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'diagnosis_review records are append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER diagnosis_review_immutable
        BEFORE UPDATE OR DELETE ON diagnosis_review
        FOR EACH ROW EXECUTE FUNCTION reject_diagnosis_review_mutation()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER diagnosis_review_immutable ON diagnosis_review")
    op.execute("DROP FUNCTION reject_diagnosis_review_mutation()")
    op.drop_table("diagnosis_review")
    op.drop_column("diagnosis", "reviewed_at")
    op.drop_column("diagnosis", "review_revision")
    op.drop_index("ix_submission_parent_submission_id", table_name="submission")
    op.drop_constraint("fk_submission_parent", "submission", type_="foreignkey")
    op.drop_column("submission", "parent_submission_id")
