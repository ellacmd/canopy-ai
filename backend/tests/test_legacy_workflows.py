from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import MetaData, Table, Uuid, select
from sqlalchemy.orm import Session

from canopy.models import Escalation, Order, OrderItem, RefundRequest
from canopy.seed import seed_id


@pytest.mark.parametrize("status,reserved", [("submitted", True), ("rejected", False)])
def test_upgrade_preserves_legacy_requests_without_inventing_confirmation(
    engine, migration_config, status, reserved
):
    command.downgrade(migration_config, "0002")
    metadata = MetaData()
    refund_table = Table("refund_requests", metadata, autoload_with=engine)
    conversation_table = Table("conversations", metadata, autoload_with=engine)
    escalation_table = Table("escalations", metadata, autoload_with=engine)
    # SQLite reflection sees UUID storage as CHAR(32); restore the logical bind type.
    for table in (refund_table, conversation_table, escalation_table):
        for column in table.columns:
            if column.name == "id" or column.name.endswith("_id"):
                column.type = Uuid()
    customer_id = seed_id("customer", "maya")
    order_id = seed_id("order", "AMZ-1024")
    refund_id, conversation_id, escalation_id = uuid4(), uuid4(), uuid4()
    created_at = datetime(2026, 11, 20, 12, tzinfo=UTC)
    with engine.begin() as connection:
        connection.execute(
            refund_table.insert().values(
                id=refund_id,
                created_at=created_at,
                customer_id=customer_id,
                order_id=order_id,
                status=status,
                amount=Decimal("10.00"),
                reason="legacy request",
                eligibility_result={},
                policy_version="legacy-v0",
                idempotency_key="legacy-refund-key",
            )
        )
        connection.execute(
            conversation_table.insert().values(
                id=conversation_id,
                customer_id=customer_id,
                status="escalated",
                created_at=created_at,
                updated_at=created_at,
            )
        )
        connection.execute(
            escalation_table.insert().values(
                id=escalation_id,
                conversation_id=conversation_id,
                customer_id=customer_id,
                order_id=order_id,
                reason="human_requested",
                summary="Legacy synthetic ticket",
                status="open",
                created_at=created_at,
            )
        )
    command.upgrade(migration_config, "head")
    with Session(engine) as db:
        refund = db.get(RefundRequest, refund_id)
        assert refund.confirmed_at is None and refund.request_fingerprint is None
        assert refund.amount == Decimal("10.00") and refund.simulated is True
        order = db.get(Order, order_id)
        assert order.refund_reserved_amount == (order.total_amount if reserved else Decimal("0"))
        item = db.scalar(select(OrderItem).where(OrderItem.order_id == order_id))
        assert item.refund_reserved_quantity == (item.quantity if reserved else 0)
        escalation = db.get(Escalation, escalation_id)
        assert escalation.updated_at == escalation.created_at
        assert escalation.idempotency_key is None
