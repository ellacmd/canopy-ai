import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session
from test_retrieval import SyntheticEmbedder
from test_workflow import FakeModel, confirmation

from canopy.config import Settings
from canopy.db import Base, make_engine
from canopy.models import (
    Conversation,
    Order,
    OrderItem,
    RefundQuote,
    RefundRequest,
    WorkflowCheckpoint,
    WorkflowTurn,
)
from canopy.retrieval import index_corpus
from canopy.seed import seed_data, seed_id
from canopy.workflow import SupportWorkflow
from canopy.workflow_contracts import TurnInput


@pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_URL"), reason="No disposable PostgreSQL configured"
)
def test_workflow_postgres_atomic_confirmation_concurrency_and_access(
    monkeypatch, migration_config
):
    url = os.environ["POSTGRES_TEST_URL"]
    monkeypatch.setenv("DATABASE_URL", url)
    settings = Settings(
        _env_file=None,
        database_url=url,
        environment="test",
        workflow_enabled=True,
        policy_search_enabled=True,
    )
    owner = make_engine(settings, runtime_role=False)
    with owner.begin() as c:
        c.execute(
            text("""DO $$ BEGIN
          IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
              CREATE ROLE anon NOLOGIN;
          END IF;
          IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN
              CREATE ROLE authenticated NOLOGIN;
          END IF;
          IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN
              CREATE ROLE service_role NOLOGIN BYPASSRLS;
          END IF;
        END $$;""")
        )
    command.upgrade(migration_config, "head")
    engine = make_engine(settings)
    cid = seed_id("customer", "maya")
    actor = uuid4()
    order_id, item_id = uuid4(), uuid4()
    with Session(engine) as db, db.begin():
        seed_data(db, datetime.now(UTC).date())
        index_corpus(db, SyntheticEmbedder())
        db.add(
            Order(
                id=order_id,
                customer_id=cid,
                order_number="AMZ-9001",
                status="delivered",
                payment_status="paid",
                total_amount=Decimal("89"),
                currency="USD",
                delivered_at=datetime.now(UTC) - timedelta(days=2),
            )
        )
        db.flush()
        db.add(
            OrderItem(
                id=item_id,
                order_id=order_id,
                product_id=seed_id("product", "HEAD-01"),
                quantity=1,
                unit_price=Decimal("89"),
            )
        )
        conversation = Conversation(customer_id=cid)
        db.add(conversation)
        db.flush()
        conv_id = conversation.id

    def workflow():
        return SupportWorkflow(
            engine,
            settings,
            customer_id=cid,
            actor_id=actor,
            model=FakeModel(),
            embedder=SyntheticEmbedder(),
        )

    try:
        preview = workflow().run(
            conv_id,
            TurnInput.model_validate(
                {
                    "selection": {
                        "order_id": str(order_id),
                        "items": [{"order_item_id": str(item_id), "quantity": 1}],
                        "reason": "change_of_mind",
                    }
                }
            ),
            str(uuid4()),
        )

        def submit(_):
            try:
                return (
                    workflow()
                    .run(
                        conv_id, TurnInput.model_validate(confirmation(preview.quote)), str(uuid4())
                    )
                    .state
                )
            except HTTPException as e:
                return e.status_code

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(submit, range(8)))
        assert "submitted" in results and set(results) <= {"submitted", 409}
        with Session(engine) as db:
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(RefundRequest)
                    .where(RefundRequest.order_id == order_id)
                )
                == 1
            )
            assert db.get(Order, order_id).refund_reserved_amount == Decimal("89")
            assert db.get(RefundQuote, preview.quote.id).status == "consumed"
            assert db.scalar(
                select(WorkflowCheckpoint).where(WorkflowCheckpoint.conversation_id == conv_id)
            )
        with owner.connect() as c:
            c.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": int.from_bytes(conv_id.bytes[:8], "big", signed=True)},
            )
            with pytest.raises(HTTPException) as error:
                workflow().run(conv_id, TurnInput(message="orders"), str(uuid4()))
            assert error.value.status_code == 409
            c.rollback()
        assert workflow().run(conv_id, TurnInput(message="Track AMZ-9001"), str(uuid4())).order
        assert (
            workflow().run(conv_id, TurnInput(message="Where is it?"), str(uuid4())).intent
            == "tracking"
        )
        with owner.connect() as c:
            assert compare_metadata(MigrationContext.configure(c), Base.metadata) == []
        for role in ["anon", "authenticated"]:
            for table in [WorkflowCheckpoint, WorkflowTurn, RefundQuote]:
                with owner.connect() as c:
                    c.execute(text(f"SET ROLE {role}"))
                    with pytest.raises(ProgrammingError):
                        c.execute(select(table))
                    c.rollback()
        with Session(engine) as db:
            q = db.get(RefundQuote, preview.quote.id)
            q.customer_id = seed_id("customer", "jordan")
            with pytest.raises(IntegrityError):
                db.commit()
    finally:
        engine.dispose()
        owner.dispose()
