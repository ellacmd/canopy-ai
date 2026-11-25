"""Actual pgvector search, access denial, atomic indexing, and migration recovery."""

import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.orm import Session
from test_retrieval import SyntheticEmbedder

from canopy.config import Settings
from canopy.db import Base, make_engine
from canopy.models import PolicyChunk, PolicyDocument
from canopy.retrieval import SearchRequest, index_corpus, search_policy


@pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_URL"), reason="No disposable PostgreSQL configured"
)
def test_pgvector_search_index_concurrency_rls_and_reversible_migration(
    monkeypatch, migration_config
):
    url = os.environ["POSTGRES_TEST_URL"]
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    settings = Settings(_env_file=None, database_url=url)
    owner = make_engine(settings, runtime_role=False)
    with owner.begin() as connection:
        connection.execute(
            text("""DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
                CREATE ROLE anon NOLOGIN;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN
                CREATE ROLE authenticated NOLOGIN;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN
                CREATE ROLE service_role NOLOGIN BYPASSRLS;
            END IF;
        END $$;""")
        )
    command.upgrade(migration_config, "head")
    runtime = make_engine(settings)
    try:

        def ingest(_):
            with Session(runtime) as db, db.begin():
                return index_corpus(db, SyntheticEmbedder())

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(ingest, range(4)))
        assert sum(result["indexed"] for result in results) == 3
        assert sum(result["skipped"] for result in results) == 9
        with Session(runtime) as db:
            assert db.scalar(select(func.count()).select_from(PolicyChunk)) == 11
            result = search_policy(db, SyntheticEmbedder(), SearchRequest(query="gift card"))
            assert result.status == "evidence_found"
            assert result.citations[0].locator == "corpus/returns-v1.0.md#excluded-items"
            assert result.citations[0].similarity == pytest.approx(1)
            assert (
                db.scalar(
                    text(
                        "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                        "WHERE attrelid='policy_chunks'::regclass AND attname='embedding'"
                    )
                )
                == "vector(768)"
            )
        with owner.connect() as connection:
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
            flags = connection.execute(
                text("""SELECT relrowsecurity, relforcerowsecurity
                FROM pg_class WHERE relname='policy_chunks' AND relnamespace='public'::regnamespace
            """)
            ).one()
            assert flags == (True, True)
        for role in ("anon", "authenticated"):
            for table in (PolicyChunk, PolicyDocument):
                with owner.connect() as connection:
                    connection.execute(text(f"SET ROLE {role}"))
                    with pytest.raises(ProgrammingError):
                        connection.execute(select(table))
                    connection.rollback()
        with Session(runtime) as db:
            chunk = db.scalar(select(PolicyChunk))
            chunk.embedding = [1.0] * 767
            with pytest.raises((DBAPIError, ValueError)):
                db.commit()
        # Downgrade removes only retrieval additions and preserves the shared extension.
        command.downgrade(migration_config, "0003")
        with owner.connect() as connection:
            assert (
                connection.scalar(text("SELECT extname FROM pg_extension WHERE extname='vector'"))
                == "vector"
            )
            assert connection.scalar(select(func.count()).select_from(PolicyDocument)) == 3
        command.upgrade(migration_config, "head")
        with Session(runtime) as db:
            # Preserved legacy records have no verified source identity after re-upgrade.
            assert (
                search_policy(db, SyntheticEmbedder(), SearchRequest(query="gift card")).status
                == "missing_evidence"
            )
        # Supabase commonly installs vector outside public. Exercise this configuration too.
        command.downgrade(migration_config, "0003")
        with owner.begin() as connection:
            connection.execute(text("CREATE SCHEMA IF NOT EXISTS extensions"))
            connection.execute(text("ALTER EXTENSION vector SET SCHEMA extensions"))
        command.upgrade(migration_config, "head")
        runtime.dispose()
        with Session(runtime) as db, db.begin():
            index_corpus(db, SyntheticEmbedder())
            assert search_policy(
                db, SyntheticEmbedder(), SearchRequest(query="gift card")
            ).citations
    finally:
        runtime.dispose()
        owner.dispose()
