"""Item-level refund reservations, confirmation, audit records, and escalation ownership.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("conversations") as batch:
        batch.create_unique_constraint("uq_conversations_id", ["id", "customer_id"])
    with op.batch_alter_table("orders") as batch:
        batch.add_column(
            sa.Column(
                "refund_reserved_amount", sa.Numeric(12, 2), nullable=False, server_default="0"
            )
        )
        batch.create_unique_constraint("uq_orders_id", ["id", "customer_id"])
        batch.create_check_constraint(
            op.f("ck_orders_refund_reservation"),
            "refund_reserved_amount >= 0 AND refund_reserved_amount <= total_amount",
        )
    with op.batch_alter_table("order_items") as batch:
        batch.add_column(
            sa.Column("refund_reserved_quantity", sa.Integer(), nullable=False, server_default="0")
        )
        batch.create_unique_constraint("uq_order_items_id", ["id", "order_id"])
        batch.create_check_constraint(
            op.f("ck_order_items_refund_reservation"),
            "refund_reserved_quantity >= 0 AND refund_reserved_quantity <= quantity",
        )
    with op.batch_alter_table("refund_requests") as batch:
        batch.add_column(sa.Column("request_fingerprint", sa.String(64), nullable=True))
        batch.add_column(sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column("customer_details", sa.Text(), nullable=False, server_default="")
        )
        batch.add_column(
            sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch.drop_constraint("uq_refund_requests_idempotency_key", type_="unique")
        batch.create_unique_constraint(
            "uq_refund_requests_customer_id", ["customer_id", "idempotency_key"]
        )
        batch.create_unique_constraint("uq_refund_requests_id", ["id", "order_id"])
        batch.drop_constraint("fk_refund_requests_order_id_orders", type_="foreignkey")
        batch.create_foreign_key(
            "fk_refund_requests_order_owner",
            "orders",
            ["order_id", "customer_id"],
            ["id", "customer_id"],
        )
        batch.create_check_constraint(op.f("ck_refund_requests_simulation_only"), "simulated")
    with op.batch_alter_table("escalations") as batch:
        batch.add_column(sa.Column("idempotency_key", sa.String(100), nullable=True))
        batch.add_column(sa.Column("request_fingerprint", sa.String(64), nullable=True))
        batch.add_column(sa.Column("resolution_note", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
        )
        batch.create_unique_constraint(
            "uq_escalations_customer_id", ["customer_id", "idempotency_key"]
        )
        batch.drop_constraint("fk_escalations_conversation_id_conversations", type_="foreignkey")
        batch.drop_constraint("fk_escalations_order_id_orders", type_="foreignkey")
        batch.create_foreign_key(
            "fk_escalations_order_owner",
            "orders",
            ["order_id", "customer_id"],
            ["id", "customer_id"],
        )
        batch.create_foreign_key(
            "fk_escalations_conversation_owner",
            "conversations",
            ["conversation_id", "customer_id"],
            ["id", "customer_id"],
        )
        batch.create_check_constraint(
            op.f("ck_escalations_status"), "status IN ('open','in_progress','resolved','closed')"
        )
    # Legacy refunds have no reliable item allocation: conservatively reserve the order.
    # Do not invent confirmation timestamps or fingerprints for historical requests.
    op.execute(
        "UPDATE orders SET refund_reserved_amount=total_amount WHERE id IN "
        "(SELECT order_id FROM refund_requests "
        "WHERE status IN ('submitted','approved','completed'))"
    )
    op.execute(
        "UPDATE order_items SET refund_reserved_quantity=quantity WHERE order_id IN "
        "(SELECT order_id FROM refund_requests "
        "WHERE status IN ('submitted','approved','completed'))"
    )
    op.execute("UPDATE escalations SET updated_at=created_at")
    op.create_table(
        "refund_lines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refund_request_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("order_item_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.UniqueConstraint(
            "refund_request_id", "order_item_id", name="uq_refund_lines_refund_request_id"
        ),
        sa.ForeignKeyConstraint(
            ["refund_request_id", "order_id"],
            ["refund_requests.id", "refund_requests.order_id"],
            name="fk_refund_lines_request_order",
        ),
        sa.ForeignKeyConstraint(
            ["order_item_id", "order_id"],
            ["order_items.id", "order_items.order_id"],
            name="fk_refund_lines_item_order",
        ),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_refund_lines_positive_quantity")),
        sa.CheckConstraint("amount > 0", name=op.f("ck_refund_lines_positive_amount")),
    )
    op.create_index("ix_refund_lines_refund_request_id", "refund_lines", ["refund_request_id"])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
    )
    op.create_index("ix_audit_events_customer_id", "audit_events", ["customer_id"])
    op.create_index("ix_audit_events_entity_id", "audit_events", ["entity_id"])
    if op.get_context().dialect.name == "postgresql":
        for table in ("refund_lines", "audit_events"):
            op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE public."{table}" FORCE ROW LEVEL SECURITY')
            op.execute(f'REVOKE ALL ON TABLE public."{table}" FROM PUBLIC, anon, authenticated')
            op.execute(
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public."{table}" TO service_role'
            )


def downgrade():
    if not op.get_context().as_sql:
        duplicates = (
            op.get_bind()
            .execute(
                sa.text(
                    "SELECT idempotency_key FROM refund_requests "
                    "GROUP BY idempotency_key HAVING COUNT(*) > 1"
                )
            )
            .first()
        )
        if duplicates:
            raise RuntimeError(
                "Cannot restore legacy global idempotency uniqueness without reconciling keys"
            )
    op.drop_table("audit_events")
    op.drop_table("refund_lines")
    with op.batch_alter_table("escalations") as batch:
        batch.drop_constraint(op.f("ck_escalations_status"), type_="check")
        batch.drop_constraint("fk_escalations_order_owner", type_="foreignkey")
        batch.drop_constraint("fk_escalations_conversation_owner", type_="foreignkey")
        batch.create_foreign_key("fk_escalations_order_id_orders", "orders", ["order_id"], ["id"])
        batch.create_foreign_key(
            "fk_escalations_conversation_id_conversations",
            "conversations",
            ["conversation_id"],
            ["id"],
        )
        batch.drop_constraint("uq_escalations_customer_id", type_="unique")
        for column in ("updated_at", "resolution_note", "request_fingerprint", "idempotency_key"):
            batch.drop_column(column)
    with op.batch_alter_table("refund_requests") as batch:
        batch.drop_constraint(op.f("ck_refund_requests_simulation_only"), type_="check")
        batch.drop_constraint("fk_refund_requests_order_owner", type_="foreignkey")
        batch.create_foreign_key(
            "fk_refund_requests_order_id_orders", "orders", ["order_id"], ["id"]
        )
        batch.drop_constraint("uq_refund_requests_customer_id", type_="unique")
        batch.drop_constraint("uq_refund_requests_id", type_="unique")
        batch.create_unique_constraint("uq_refund_requests_idempotency_key", ["idempotency_key"])
        for column in ("simulated", "customer_details", "confirmed_at", "request_fingerprint"):
            batch.drop_column(column)
    with op.batch_alter_table("order_items") as batch:
        batch.drop_constraint(op.f("ck_order_items_refund_reservation"), type_="check")
        batch.drop_constraint("uq_order_items_id", type_="unique")
        batch.drop_column("refund_reserved_quantity")
    with op.batch_alter_table("orders") as batch:
        batch.drop_constraint(op.f("ck_orders_refund_reservation"), type_="check")
        batch.drop_constraint("uq_orders_id", type_="unique")
        batch.drop_column("refund_reserved_amount")
    with op.batch_alter_table("conversations") as batch:
        batch.drop_constraint("uq_conversations_id", type_="unique")
