from datetime import date
from decimal import Decimal
from io import StringIO

import httpx
import pytest
from alembic import command
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from canopy.api import create_app
from canopy.auth import SupabaseAuth
from canopy.config import Settings
from canopy.db import Base
from canopy.models import Customer, Order, OrderItem
from canopy.seed import seed_data, seed_id


def test_seed_is_repeatable_and_preserves_auth_links(engine):
    with Session(engine) as db, db.begin():
        before = db.get(Customer, seed_id("customer", "maya")).auth_user_id
        for _ in range(2):
            assert seed_data(db, date(2026, 11, 24)) == {
                "customers": 3,
                "products": 4,
                "orders": 11,
            }
        assert db.get(Customer, seed_id("customer", "maya")).auth_user_id == before
        orders = db.scalars(select(Order)).all()
        for order in orders:
            items = db.scalars(select(OrderItem).where(OrderItem.order_id == order.id)).all()
            assert sum(item.quantity * item.unit_price for item in items) == order.total_amount


@pytest.mark.parametrize(
    "field,value",
    [
        ("total_amount", Decimal("-1.00")),
        ("status", "invented-status"),
        ("currency", "US"),
        ("payment_status", "imaginary-payment"),
    ],
)
def test_database_rejects_invalid_order_values(engine, field, value):
    with Session(engine) as db:
        order = db.get(Order, seed_id("order", "AMZ-1024"))
        setattr(order, field, value)
        with pytest.raises(IntegrityError):
            db.commit()


def test_migration_downgrade_and_reupgrade(engine, migration_config):
    command.downgrade(migration_config, "base")
    assert inspect(engine).get_table_names() == ["alembic_version"]
    command.upgrade(migration_config, "head")
    assert set(Base.metadata.tables) <= set(inspect(engine).get_table_names())


def test_postgres_migrations_compile_without_connecting(migration_config, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://test:test@localhost/canopy")
    output = StringIO()
    migration_config.output_buffer = output
    command.upgrade(migration_config, "head", sql=True)
    sql = output.getvalue()
    assert "UUID" in sql and "TIMESTAMP WITH TIME ZONE" in sql
    for table in [*Base.metadata.tables, "alembic_version"]:
        assert f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY' in sql
        assert f'REVOKE ALL ON TABLE public."{table}" FROM PUBLIC, anon, authenticated' in sql
    assert "CREATE POLICY" not in sql


def test_readiness_fails_before_migrations(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503


@pytest.mark.parametrize("status,expected", [(401, 401), (500, 503), (429, 429)])
def test_auth_upstream_failures_are_safe(settings, status, expected):
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, json={"error": "private upstream detail"})
        )
    ) as client:
        with pytest.raises(HTTPException) as error:
            SupabaseAuth(settings, client).verify("private-token")
        assert error.value.status_code == expected
        assert "private upstream detail" not in str(error.value.detail)


def test_auth_network_failure_is_safe(settings):
    def fail(request):
        raise httpx.ReadTimeout("private-token", request=request)

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(HTTPException) as error:
            SupabaseAuth(settings, client).verify("private-token")
        assert error.value.status_code == 503
        assert "private-token" not in str(error.value.detail)


def test_settings_hide_secrets_and_require_auth_pair():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None, supabase_url="https://demo.supabase.co", supabase_publishable_key=""
        )
    settings = Settings(
        _env_file=None,
        database_url="sqlite:///secret-database",
        gemini_api_key="private-gemini-key",
    )
    assert "private-gemini-key" not in repr(settings)
    assert "secret-database" not in repr(settings)


def test_production_requires_postgres_and_https():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="production", database_url="sqlite://")
