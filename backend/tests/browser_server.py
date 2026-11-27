"""Disposable FastAPI server for browser tests, with synthetic external providers."""

import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from time import time
from uuid import UUID, uuid4

import httpx
import uvicorn
from alembic import command
from alembic.config import Config
from fastapi import Depends
from sqlalchemy.orm import Session
from test_retrieval import SyntheticEmbedder
from test_voice import browser_live
from test_workflow import FakeModel

from canopy.api import create_app
from canopy.auth import Principal, SupabaseAuth, current_customer, current_principal, get_auth
from canopy.config import Settings
from canopy.models import Customer
from canopy.policy_api import get_embedder
from canopy.retrieval import index_corpus
from canopy.seed import seed_data, seed_id
from canopy.voice import get_live_connector
from canopy.workflow import SupportWorkflow
from canopy.workflow_api import get_workflow

ACCOUNTS = {
    "maya@amazora.example": {"id": UUID(int=1), "email": "maya@amazora.example"},
    "jordan@amazora.example": {"id": UUID(int=2), "email": "jordan@amazora.example"},
    "amara@amazora.example": {"id": UUID(int=4), "email": "amara@amazora.example"},
    "support@amazora.example": {
        "id": UUID(int=3),
        "email": "support@amazora.example",
        "app_metadata": {"role": "support"},
    },
    "admin@amazora.example": {
        "id": UUID(int=5),
        "email": "admin@amazora.example",
        "app_metadata": {"role": "admin"},
    },
}


def auth_provider(path: Path):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                access TEXT PRIMARY KEY, refresh TEXT UNIQUE NOT NULL, email TEXT NOT NULL,
                access_until INTEGER NOT NULL, refresh_until INTEGER NOT NULL
            )
        """)

    def digest(token: str) -> str:
        return sha256(token.encode()).hexdigest()

    def handle(request: httpx.Request) -> httpx.Response:
        bearer = digest(request.headers.get("authorization", "").removeprefix("Bearer "))
        now = int(time())
        with closing(sqlite3.connect(path)) as db, db:
            if request.url.path.endswith("/user"):
                row = db.execute(
                    "SELECT email FROM sessions WHERE access = ? AND access_until > ?",
                    (bearer, now),
                ).fetchone()
                account = ACCOUNTS.get(row[0]) if row else None
                return (
                    httpx.Response(200, json={**account, "id": str(account["id"])})
                    if account
                    else httpx.Response(401)
                )
            if request.url.path.endswith("/logout"):
                db.execute("DELETE FROM sessions WHERE access = ?", (bearer,))
                return httpx.Response(204)
            if not request.url.path.endswith("/token"):
                return httpx.Response(404)
            body = json.loads(request.content)
            if request.url.params["grant_type"] == "refresh_token":
                row = db.execute(
                    "DELETE FROM sessions WHERE refresh = ? AND refresh_until > ? RETURNING email",
                    (digest(body.get("refresh_token") or ""), now),
                ).fetchone()
                email = row[0] if row else None
            else:
                email = body.get("email") if body.get("password") == "synthetic-password" else None
            if email not in ACCOUNTS:
                return httpx.Response(401)
            access_token, refresh_token = uuid4().hex, uuid4().hex
            db.execute("DELETE FROM sessions WHERE refresh_until <= ?", (now,))
            db.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
                (digest(access_token), digest(refresh_token), email, now + 3600, now + 604800),
            )
        return httpx.Response(
            200,
            json={"access_token": access_token, "refresh_token": refresh_token, "expires_in": 3600},
        )

    return handle


def main():
    with (
        TemporaryDirectory() as temporary,
        httpx.Client(
            transport=httpx.MockTransport(auth_provider(Path(temporary) / "demo-sessions.db"))
        ) as upstream,
    ):
        settings = Settings(
            _env_file=None,
            environment="test",
            database_url=f"sqlite:///{Path(temporary) / 'canopy.db'}",
            supabase_url="https://auth.amazora.example",
            supabase_publishable_key="test-key",
            allowed_origins=["http://127.0.0.1:3100"],
            gemini_api_key="",
            langsmith_api_key="",
            workflow_enabled=True,
            voice_enabled=True,
            policy_search_enabled=True,
            workflow_tracing_enabled=False,
        )
        app = create_app(settings)
        engine = app.state.engine
        os.environ.update(ENVIRONMENT="test", DATABASE_URL=settings.database_url.get_secret_value())
        command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")
        embedder = SyntheticEmbedder()
        with Session(engine) as db, db.begin():
            seed_data(db, datetime.now(UTC).date())
            for key in ("maya", "jordan", "amara"):
                db.get(Customer, seed_id("customer", key)).auth_user_id = ACCOUNTS[
                    f"{key}@amazora.example"
                ]["id"]
            index_corpus(db, embedder)

        def workflow(
            customer: Customer = Depends(current_customer),
            principal: Principal = Depends(current_principal),
        ):
            config = settings
            if principal.id == ACCOUNTS["amara@amazora.example"]["id"]:
                config = settings.model_copy(update={"gemini_chat_calls_per_day": 0})
            return SupportWorkflow(
                engine,
                config,
                customer_id=customer.id,
                actor_id=principal.id,
                model=FakeModel(),
                embedder=embedder,
            )

        app.dependency_overrides[get_auth] = lambda: SupabaseAuth(settings, upstream)
        app.dependency_overrides[get_live_connector] = lambda: browser_live
        app.dependency_overrides[get_workflow] = workflow
        app.dependency_overrides[get_embedder] = lambda: embedder
        uvicorn.run(app, host="127.0.0.1", port=8010, log_level="warning")


if __name__ == "__main__":
    main()
