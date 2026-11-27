"""Keep a history of published refund policies.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "refund_policy_revisions",
        sa.Column("revision", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_by", sa.Uuid()),
        sa.CheckConstraint("revision > 0", name="positive_revision"),
    )
    op.execute(
        "INSERT INTO refund_policy_revisions (revision, settings, published_at) "
        "VALUES (1, '{}', CURRENT_TIMESTAMP)"
    )
    if op.get_context().dialect.name == "postgresql":
        op.execute('ALTER TABLE public."refund_policy_revisions" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE public."refund_policy_revisions" FORCE ROW LEVEL SECURITY')
        op.execute(
            'REVOKE ALL ON TABLE public."refund_policy_revisions" FROM PUBLIC, anon, authenticated'
        )
        op.execute(
            'GRANT SELECT, INSERT, UPDATE ON TABLE public."refund_policy_revisions" TO service_role'
        )


def downgrade():
    op.drop_table("refund_policy_revisions")
