"""Financial-safety tests on actual PostgreSQL row locks and constraints."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from canopy.config import Settings
from canopy.contracts import RefundInput
from canopy.db import make_engine
from canopy.models import AuditEvent, Order, OrderItem, RefundLine, RefundRequest
from canopy.policy import POLICY_VERSION
from canopy.seed import seed_data, seed_id
from canopy.tools import SupportTools


@pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_URL"), reason="No disposable PostgreSQL configured"
)
def test_postgres_concurrent_refunds_and_database_ownership(monkeypatch, migration_config):
    url = os.environ["POSTGRES_TEST_URL"]
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    settings = Settings(_env_file=None, database_url=url)
    owner = make_engine(settings, runtime_role=False)
    with owner.begin() as connection:
        connection.execute(
            text(
                """DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
                CREATE ROLE anon NOLOGIN;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN
                CREATE ROLE authenticated NOLOGIN;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN
                CREATE ROLE service_role NOLOGIN BYPASSRLS;
            END IF;
        END $$;"""
            )
        )
    command.upgrade(migration_config, "head")
    engine = make_engine(settings)
    actor = uuid4()
    with Session(engine) as db, db.begin():
        seed_data(db, date(2026, 11, 24))
        for number in ["AMZ-1024", "AMZ-1026", "AMZ-1034"]:
            db.get(Order, seed_id("order", number)).delivered_at = datetime.now(UTC) - timedelta(
                days=5
            )

    def request(number):
        return RefundInput.model_validate(
            {
                "order_id": str(seed_id("order", number)),
                "items": [{"order_item_id": str(seed_id("item", number)), "quantity": 1}],
                "reason": "change_of_mind",
                "confirmed": True,
                "policy_version": POLICY_VERSION,
            }
        )

    def submit(number, key):
        with Session(engine) as db:
            tools = SupportTools(db, customer_id=seed_id("customer", "maya"), actor_user_id=actor)
            try:
                result = tools.create_refund_request(request(number), key)
                return {"id": result.id, "replayed": result.replayed}
            except HTTPException as error:
                return {"status": error.status_code}

    try:
        # Eight independent database sessions race to reserve one physical item.
        with ThreadPoolExecutor(max_workers=8) as pool:
            raced = list(pool.map(lambda _: submit("AMZ-1024", str(uuid4())), range(8)))
        winners = [result for result in raced if "id" in result]
        assert len(winners) == 1
        assert all(result.get("status") == 409 for result in raced if "id" not in result)
        # Concurrent retries of one operation all observe the same durable request.
        key = str(uuid4())
        with ThreadPoolExecutor(max_workers=8) as pool:
            retried = list(pool.map(lambda _: submit("AMZ-1034", key), range(8)))
        assert len({result["id"] for result in retried}) == 1
        assert sum(not result["replayed"] for result in retried) == 1
        # Two units support two partial returns, but no third reservation.
        with ThreadPoolExecutor(max_workers=2) as pool:
            partials = list(pool.map(lambda _: submit("AMZ-1026", str(uuid4())), range(2)))
        assert all("id" in result for result in partials)
        assert submit("AMZ-1026", str(uuid4())) == {"status": 409}
        with Session(engine) as db:
            assert db.get(OrderItem, seed_id("item", "AMZ-1026")).refund_reserved_quantity == 2
            assert db.get(Order, seed_id("order", "AMZ-1026")).refund_reserved_amount == Decimal(
                "48.00"
            )
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(AuditEvent.event_type == "refund_request_created")
                )
                == 4
            )
            # A raw SQL/ORM write still cannot attach another order's item to a request.
            db.add(
                RefundLine(
                    refund_request_id=winners[0]["id"],
                    order_id=seed_id("order", "AMZ-1024"),
                    order_item_id=seed_id("item", "AMZ-1027"),
                    quantity=1,
                    amount=Decimal("1.00"),
                )
            )
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()
            db.add(
                RefundRequest(
                    order_id=seed_id("order", "AMZ-1024"),
                    customer_id=seed_id("customer", "jordan"),
                    amount=Decimal("1.00"),
                    reason="change_of_mind",
                    eligibility_result={},
                    policy_version=POLICY_VERSION,
                    idempotency_key=str(uuid4()),
                    request_fingerprint="0" * 64,
                    confirmed_at=datetime.now(UTC),
                    simulated=True,
                )
            )
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()
    finally:
        engine.dispose()
        owner.dispose()
