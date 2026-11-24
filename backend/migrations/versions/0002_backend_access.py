"""Deny direct client database access; the backend uses service_role.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Freeze this list with the migration rather than importing mutable application models.
TABLES = (
    "customers",
    "products",
    "orders",
    "order_items",
    "shipments",
    "conversations",
    "messages",
    "refund_requests",
    "policy_documents",
    "escalations",
    "agent_executions",
    "alembic_version",
)


def upgrade() -> None:
    if op.get_context().dialect.name != "postgresql":
        return
    # These are standard Supabase roles. Plain PostgreSQL must bootstrap them first.
    op.execute("GRANT USAGE ON SCHEMA public TO service_role")
    for table in TABLES:
        op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE public."{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON TABLE public."{table}" FROM PUBLIC, anon, authenticated')
        op.execute(
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public."{table}" TO service_role'
        )


def downgrade() -> None:
    if op.get_context().dialect.name != "postgresql":
        return
    for table in TABLES:
        op.execute(f'ALTER TABLE public."{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE public."{table}" DISABLE ROW LEVEL SECURITY')
    # Never restore direct client grants as a side effect of a downgrade.
