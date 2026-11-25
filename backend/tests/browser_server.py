"""Disposable FastAPI server for browser tests, with synthetic external providers."""

import json
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

import httpx
import uvicorn
from fastapi import Depends
from sqlalchemy.orm import Session
from test_retrieval import SyntheticEmbedder
from test_workflow import FakeModel

from canopy.api import create_app
from canopy.auth import Principal, SupabaseAuth, current_customer, current_principal, get_auth
from canopy.config import Settings
from canopy.db import Base
from canopy.models import Customer
from canopy.policy_api import get_embedder
from canopy.retrieval import index_corpus
from canopy.seed import seed_data, seed_id
from canopy.workflow import SupportWorkflow
from canopy.workflow_api import get_workflow

ACCOUNTS = {
    "maya@amazora.example": {"id": UUID(int=1), "email": "maya@amazora.example"},
    "jordan@amazora.example": {"id": UUID(int=2), "email": "jordan@amazora.example"},
    "support@amazora.example": {
        "id": UUID(int=3),
        "email": "support@amazora.example",
        "app_metadata": {"role": "support"},
    },
}


def auth_provider():
    access: dict[str, str] = {}
    refresh: dict[str, str] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
        if request.url.path.endswith("/user"):
            email = access.get(bearer)
            if email is None:
                return httpx.Response(401)
            return httpx.Response(200, json={**ACCOUNTS[email], "id": str(ACCOUNTS[email]["id"])})
        if request.url.path.endswith("/logout"):
            email = access.pop(bearer, None)
            for token in [key for key, value in refresh.items() if value == email]:
                del refresh[token]
            return httpx.Response(204)
        if not request.url.path.endswith("/token"):
            return httpx.Response(404)
        body = json.loads(request.content)
        if request.url.params["grant_type"] == "refresh_token":
            email = refresh.pop(body.get("refresh_token"), None)
        else:
            email = body.get("email") if body.get("password") == "synthetic-password" else None
        if email not in ACCOUNTS:
            return httpx.Response(401)
        access_token, refresh_token = uuid4().hex, uuid4().hex
        access[access_token] = refresh[refresh_token] = email
        return httpx.Response(
            200,
            json={"access_token": access_token, "refresh_token": refresh_token, "expires_in": 3600},
        )

    return handle


def main():
    with (
        TemporaryDirectory() as temporary,
        httpx.Client(transport=httpx.MockTransport(auth_provider())) as upstream,
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
            policy_search_enabled=True,
            workflow_tracing_enabled=False,
        )
        app = create_app(settings)
        engine = app.state.engine
        Base.metadata.create_all(engine)
        embedder = SyntheticEmbedder()
        with Session(engine) as db, db.begin():
            seed_data(db, datetime.now(UTC).date())
            for key in ("maya", "jordan"):
                db.get(Customer, seed_id("customer", key)).auth_user_id = ACCOUNTS[
                    f"{key}@amazora.example"
                ]["id"]
            index_corpus(db, embedder)

        def workflow(
            customer: Customer = Depends(current_customer),
            principal: Principal = Depends(current_principal),
        ):
            return SupportWorkflow(
                engine,
                settings,
                customer_id=customer.id,
                actor_id=principal.id,
                model=FakeModel(),
                embedder=embedder,
            )

        app.dependency_overrides[get_auth] = lambda: SupabaseAuth(settings, upstream)
        app.dependency_overrides[get_workflow] = workflow
        app.dependency_overrides[get_embedder] = lambda: embedder
        uvicorn.run(app, host="127.0.0.1", port=8010, log_level="warning")


if __name__ == "__main__":
    main()
