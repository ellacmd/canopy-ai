"""Runs against a dedicated disposable PostgreSQL database in CI, never a user database."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from canopy.admission import DAY, Admission, Limit, LimitExceeded
from canopy.config import Settings
from canopy.db import Base, make_engine
from canopy.models import AdmissionCounter, Customer
from canopy.seed import seed_data


@pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_URL"), reason="No disposable PostgreSQL configured"
)
def test_postgres_migrations_seed_rls_and_runtime_role(monkeypatch, migration_config):
    url = os.environ["POSTGRES_TEST_URL"]
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    settings = Settings(_env_file=None, database_url=url)
    owner = make_engine(settings, runtime_role=False)
    # Role bootstrap belongs only in this disposable test environment.
    with owner.begin() as connection:
        connection.execute(
            text("""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
                    CREATE ROLE anon NOLOGIN;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN
                    CREATE ROLE authenticated NOLOGIN;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN
                    CREATE ROLE service_role NOLOGIN BYPASSRLS;
                END IF;
            END $$;
        """)
        )
    command.upgrade(migration_config, "head")
    runtime = make_engine(settings)
    with Session(runtime) as db, db.begin():
        assert db.scalar(text("SELECT current_user")) == "service_role"
        assert seed_data(db, date(2026, 11, 24))["orders"] == 11
    with owner.connect() as connection:
        flags = connection.execute(
            text("""
            SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class
            WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'
        """)
        ).all()
        assert all(
            enabled and forced
            for name, enabled, forced in flags
            if name in Base.metadata.tables or name == "alembic_version"
        )
        assert (
            connection.scalar(text("SELECT count(*) FROM pg_policies WHERE schemaname='public'"))
            == 0
        )
    for role in ("anon", "authenticated"):
        with owner.connect() as connection:
            for table in (Customer, AdmissionCounter):
                connection.execute(text(f"SET ROLE {role}"))
                with pytest.raises(ProgrammingError):
                    connection.execute(select(table))
                connection.rollback()

    second_runtime = make_engine(settings)
    replicas = [Admission(runtime, settings), Admission(second_runtime, settings)]
    bucket = Limit("test-concurrent-reservations", str(uuid4()), 7, DAY)

    def reserve(index):
        try:
            replicas[index % len(replicas)].take(bucket)
        except LimitExceeded:
            return False
        return True

    try:
        with ThreadPoolExecutor(max_workers=12) as workers:
            assert sum(workers.map(reserve, range(32))) == 7
    finally:
        second_runtime.dispose()
    runtime.dispose()
    owner.dispose()
