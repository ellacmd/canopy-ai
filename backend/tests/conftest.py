from datetime import date
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from canopy.api import create_app
from canopy.auth import SupabaseAuth
from canopy.config import Settings
from canopy.db import make_engine
from canopy.models import Customer
from canopy.seed import seed_data, seed_id

MAYA_AUTH = UUID("00000000-0000-4000-8000-000000000001")
JORDAN_AUTH = UUID("00000000-0000-4000-8000-000000000002")
ADMIN_AUTH = UUID("00000000-0000-4000-8000-000000000003")


@pytest.fixture
def settings(tmp_path, monkeypatch):
    values = {
        "ENVIRONMENT": "test",
        "DATABASE_URL": f"sqlite:///{tmp_path / 'canopy.db'}",
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_PUBLISHABLE_KEY": "test-key",
        "GEMINI_API_KEY": "",
        "POLICY_SEARCH_ENABLED": "false",
        "WORKFLOW_ENABLED": "false",
        "WORKFLOW_TRACING_ENABLED": "false",
        "LANGSMITH_API_KEY": "",
        "ALLOWED_ORIGINS": '["http://localhost:3000","http://127.0.0.1:8000"]',
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


@pytest.fixture
def migration_config():
    return Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))


@pytest.fixture
def engine(settings, migration_config):
    command.upgrade(migration_config, "head")
    engine = make_engine(settings)
    with Session(engine) as db, db.begin():
        seed_data(db, date(2026, 11, 24))
        db.get(Customer, seed_id("customer", "maya")).auth_user_id = MAYA_AUTH
        db.get(Customer, seed_id("customer", "jordan")).auth_user_id = JORDAN_AUTH
    yield engine
    engine.dispose()


def supabase_mock(request: httpx.Request) -> httpx.Response:
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    users = {
        "maya-token": {"id": str(MAYA_AUTH), "email": "maya@amazora.example"},
        "jordan-token": {"id": str(JORDAN_AUTH), "email": "jordan@amazora.example"},
        "admin-token": {
            "id": str(ADMIN_AUTH),
            "email": "admin@amazora.example",
            "app_metadata": {"role": "admin"},
        },
        "support-token": {"id": str(ADMIN_AUTH), "app_metadata": {"role": "support"}},
        "forged-role": {"id": str(MAYA_AUTH), "user_metadata": {"role": "admin"}},
        "unknown-role": {"id": str(ADMIN_AUTH), "app_metadata": {"role": "superuser"}},
        "unlinked-token": {"id": "00000000-0000-4000-8000-000000000099"},
    }
    if request.url.path.endswith("/user"):
        return httpx.Response(200, json=users[token]) if token in users else httpx.Response(401)
    if request.url.path.endswith("/token"):
        import json

        body = json.loads(request.content)
        valid = (
            body.get("password") == "synthetic-password" or body.get("refresh_token") == "refresh"
        )
        if valid:
            return httpx.Response(
                200,
                json={
                    "access_token": "maya-token",
                    "refresh_token": "refresh",
                    "expires_in": 3600,
                },
            )
        return httpx.Response(400, json={"message": "Provider-specific private message"})
    if request.url.path.endswith("/logout"):
        return httpx.Response(204)
    return httpx.Response(500)


@pytest.fixture
def client(settings, engine):
    app = create_app(settings)
    with httpx.Client(transport=httpx.MockTransport(supabase_mock)) as upstream:
        with TestClient(app) as client:
            app.state.auth = SupabaseAuth(settings, upstream)
            yield client
