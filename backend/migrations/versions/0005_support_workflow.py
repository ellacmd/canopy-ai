"""Atomic support turns, owned checkpoints, and expiring refund confirmation quotes.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def common():
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), sa.ForeignKey("customers.id"), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id", "customer_id"], ["conversations.id", "conversations.customer_id"]
        ),
    ]


def upgrade():
    op.add_column(
        "agent_executions", sa.Column("details", sa.JSON(), nullable=False, server_default="{}")
    )
    op.create_table(
        "workflow_checkpoints",
        *common(),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.UniqueConstraint("conversation_id"),
    )
    op.create_table(
        "workflow_turns",
        *common(),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.UniqueConstraint("conversation_id", "idempotency_key"),
    )
    op.create_table(
        "refund_quotes",
        *common(),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("selection", sa.JSON(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("policy_version", sa.String(40), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("refund_request_id", sa.Uuid(), sa.ForeignKey("refund_requests.id")),
        sa.ForeignKeyConstraint(["order_id", "customer_id"], ["orders.id", "orders.customer_id"]),
        sa.CheckConstraint("status IN ('pending','consumed','cancelled')", name="status"),
        sa.CheckConstraint("amount > 0", name="amount"),
    )
    for table in ("workflow_checkpoints", "workflow_turns", "refund_quotes"):
        op.create_index(f"ix_{table}_customer_id", table, ["customer_id"])
        if op.get_context().dialect.name == "postgresql":
            op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE public."{table}" FORCE ROW LEVEL SECURITY')
            op.execute(f'REVOKE ALL ON TABLE public."{table}" FROM PUBLIC, anon, authenticated')
            op.execute(
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public."{table}" TO service_role'
            )
    op.create_index("ix_refund_quotes_conversation_id", "refund_quotes", ["conversation_id"])


def downgrade():
    for table in ("refund_quotes", "workflow_turns", "workflow_checkpoints"):
        op.drop_table(table)
    with op.batch_alter_table("agent_executions") as batch:
        batch.drop_column("details")
