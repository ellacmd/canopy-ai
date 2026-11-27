"""Shared request and provider usage counters.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "admission_counters",
        sa.Column("bucket", sa.String(64), primary_key=True),
        sa.Column("expires_at", sa.BigInteger(), primary_key=True),
        sa.Column("used", sa.Integer(), nullable=False),
        sa.CheckConstraint("used > 0", name="positive_used"),
    )
    op.create_index("ix_admission_counters_expires_at", "admission_counters", ["expires_at"])
    if op.get_context().dialect.name == "postgresql":
        op.execute('ALTER TABLE public."admission_counters" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE public."admission_counters" FORCE ROW LEVEL SECURITY')
        op.execute(
            'REVOKE ALL ON TABLE public."admission_counters" FROM PUBLIC, anon, authenticated'
        )
        op.execute(
            'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public."admission_counters" '
            "TO service_role"
        )


def downgrade():
    op.drop_table("admission_counters")
