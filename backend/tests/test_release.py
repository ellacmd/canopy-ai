import json
from uuid import uuid4

import httpx
import pytest
from alembic import command
from conftest import supabase_mock
from fastapi import Depends
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_retrieval import SyntheticEmbedder
from test_workflow import ACTOR, FakeModel

from canopy.admission import DAY, Admission, LimitExceeded
from canopy.api import create_app
from canopy.auth import SupabaseAuth, current_customer
from canopy.config import Settings
from canopy.db import make_engine
from canopy.llm import ModelUnavailable
from canopy.models import AdmissionCounter, AgentExecution, WorkflowTurn
from canopy.policy_api import get_embedder
from canopy.retrieval import index_corpus
from canopy.workflow import SupportWorkflow
from canopy.workflow_api import get_workflow


def test_login_limit_shared_and_window_resets(engine, settings, monkeypatch):
    clock = [DAY + 10]
    monkeypatch.setattr("canopy.admission.database_epoch", lambda connection: clock[0])
    config = settings.model_copy(update={"login_attempts_per_account_minute": 1})
    first, second = create_app(config), create_app(config)
    with (
        httpx.Client(transport=httpx.MockTransport(supabase_mock)) as upstream,
        TestClient(first, client=("192.0.2.1", 4000)) as one,
        TestClient(second, client=("192.0.2.2", 4000)) as two,
    ):
        first.state.auth = second.state.auth = SupabaseAuth(config, upstream)
        login = "/api/auth/login"
        assert (
            one.post(login, json={"email": "test@example.net", "password": "wrong"}).status_code
            == 401
        )
        response = two.post(
            login,
            json={"email": "TEST@example.net", "password": "wrong"},
            headers={"X-Forwarded-For": "192.0.2.3"},
        )
        assert response.status_code == 429 and response.headers["retry-after"] == "50"
        with Session(engine) as db:
            counters = db.scalars(select(AdmissionCounter)).all()
            assert len(counters) == 3
            assert all(len(row.bucket) == 64 and row.used == 1 for row in counters)
        clock[0] += DAY + 50
        assert Admission(engine, config).prune() == 3
        assert (
            two.post(login, json={"email": "test@example.net", "password": "wrong"}).status_code
            == 401
        )


def test_global_budget_cannot_be_bypassed_by_actor_or_secret_rotation(
    engine, settings, monkeypatch
):
    clock = [DAY - 1]
    monkeypatch.setattr("canopy.admission.database_epoch", lambda connection: clock[0])
    config = settings.model_copy(update={"gemini_chat_calls_per_day": 1})
    other_engine = make_engine(config)
    try:
        admission = Admission(engine, config)
        rotated = Admission(
            other_engine,
            config.model_copy(update={"admission_key": type(config.admission_key)("rotated")}),
        )
        admission.provider("chat", uuid4())
        with pytest.raises(LimitExceeded) as error:
            rotated.provider("chat", uuid4())
        assert error.value.code == "daily_usage_budget" and error.value.retry_after == 1
        rotated.provider("embedding", uuid4())
        clock[0] += 1
        rotated.provider("chat", uuid4())
    finally:
        other_engine.dispose()


def test_failed_provider_attempt_spends_budget_and_sse_refusal_is_safe(client, engine):
    settings = client.app.state.settings.model_copy(update={"gemini_chat_calls_per_day": 1})
    admission = Admission(engine, settings)
    calls = []

    class FailingModel:
        def generate_structured(self, *args, **kwargs):
            calls.append(1)
            raise ModelUnavailable("synthetic-private-provider-detail")

    def workflow(customer=Depends(current_customer)):
        return SupportWorkflow(
            engine,
            settings,
            customer_id=customer.id,
            actor_id=ACTOR,
            model=FailingModel(),
            embedder=None,
            admission=admission,
        )

    client.app.dependency_overrides[get_workflow] = workflow
    headers = {"Authorization": "Bearer maya-token", "Idempotency-Key": "test-provider-failure"}
    conversation = client.post("/api/conversations", headers=headers).json()["id"]
    url = f"/api/conversations/{conversation}/turns"
    request = {"message": "Show my orders"}
    response = client.post(url, headers=headers, json=request)
    assert response.status_code == 503 and "synthetic-private" not in response.text
    response = client.post(url + "/stream", headers=headers, json=request)
    event = json.loads(response.text.split("data: ", 1)[1])
    assert event["status"] == 429 and event["code"] == "daily_usage_budget"
    assert len(calls) == 1
    with Session(engine) as db:
        execution = db.scalar(select(AgentExecution))
        assert execution.details["model_calls"] == 1
        assert execution.details["usage_complete"] is False
        assert not db.scalars(select(WorkflowTurn)).all()


def test_replay_and_explicit_action_work_at_ai_budget_limit(client, engine, monkeypatch):
    monkeypatch.setattr("canopy.workflow.export_trace", lambda *a, **kw: "failed")
    settings = client.app.state.settings.model_copy(
        update={"gemini_chat_calls_per_day": 2, "workflow_tracing_enabled": True}
    )
    model = FakeModel()

    def workflow(customer=Depends(current_customer)):
        return SupportWorkflow(
            engine, settings, customer_id=customer.id, actor_id=ACTOR, model=model, embedder=None
        )

    client.app.dependency_overrides[get_workflow] = workflow
    headers = {"Authorization": "Bearer maya-token", "Idempotency-Key": "test-budget-replay"}
    conversation = client.post("/api/conversations", headers=headers).json()["id"]
    url = f"/api/conversations/{conversation}/turns"
    request = {"message": "Show my orders"}
    assert client.post(url, headers=headers, json=request).status_code == 200
    assert client.post(url, headers=headers, json=request).json()["replayed"] is True
    headers["Idempotency-Key"] = "budget-exhausted"
    response = client.post(url, headers=headers, json=request)
    assert response.status_code == 429 and int(response.headers["retry-after"]) > 0
    headers["Idempotency-Key"] = "explicit-escalation"
    assert (
        client.post(url, headers=headers, json={"action": "escalate"}).json()["state"]
        == "escalated"
    )
    assert model.calls == ["Classification", "GroundedReply"]
    response = client.get(
        "/api/admin/executions", headers={"Authorization": "Bearer support-token"}
    )
    assert [row["trace_delivery"] for row in response.json()] == ["failed", "failed"]


def test_unindexed_policy_uses_no_budget_and_indexed_policy_obeys_zero_budget(client, engine):
    client.app.state.admission.settings = client.app.state.settings.model_copy(
        update={"gemini_embedding_calls_per_day": 0}
    )
    embedder = SyntheticEmbedder()
    client.app.dependency_overrides[get_embedder] = lambda: embedder
    headers = {"Authorization": "Bearer maya-token"}
    request = {"query": "Can I return a gift card?"}
    url = "/api/policies/search"
    assert client.post(url, headers=headers, json=request).status_code == 200
    with Session(engine) as db, db.begin():
        index_corpus(db, embedder)
    assert client.post(url, headers=headers, json=request).status_code == 429
    assert "query" not in embedder.calls


def test_old_schema_fails_readiness_and_admission_then_upgrade_recovers(client, migration_config):
    command.downgrade(migration_config, "0005")
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 503
    response = client.post(
        "/api/auth/login", json={"email": "test@example.net", "password": "synthetic-password"}
    )
    assert response.status_code == 503 and "admission_counters" not in response.text
    command.upgrade(migration_config, "head")
    assert client.get("/health/ready").status_code == 200


def test_production_configuration_and_http_redaction(settings):
    production = {
        "environment": "production",
        "database_url": "postgresql+psycopg://runtime@db.example.net/db?sslmode=verify-full",
        "allowed_origins": ["https://canopy.example.net"],
        "admission_key": "x" * 32,
    }
    config = Settings(_env_file=None, **production)
    for invalid in (
        {"database_url": "postgresql+psycopg://runtime@db.example.net/db?sslmode=require"},
        {"allowed_origins": []},
        {"allowed_origins": ["https://canopy.example.net/path"]},
        {"allowed_origins": ["https://canopy.example.net?"]},
        {"admission_key": "short"},
        {"policy_search_enabled": True},
        {"workflow_tracing_enabled": True},
    ):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **{**production, **invalid})
    # HTTP enforcement is tested independently of the production connection validator.
    config = config.model_copy(update={"database_url": settings.database_url})
    with (
        httpx.Client(transport=httpx.MockTransport(supabase_mock)) as upstream,
        TestClient(create_app(config), base_url="https://canopy.example.net") as client,
    ):
        from canopy.db import Base

        Base.metadata.create_all(client.app.state.engine)
        client.app.state.auth = SupabaseAuth(config, upstream)
        assert client.get("/openapi.json").status_code == 404
        login = "/api/auth/login"
        body = {"email": "maya@amazora.example", "password": "synthetic-password"}
        invalid = client.post(login, json={**body, "private-token": "another-secret"})
        assert invalid.status_code == 422 and not any(
            token in invalid.text
            for token in ("private-token", "another-secret", "synthetic-password")
        )
        blocked = client.post(login, json=body, headers={"Origin": "https://other.example.net"})
        assert blocked.status_code == 403 and blocked.headers["cache-control"] == "no-store"
        response = client.post(
            login, json=body, headers={"Origin": production["allowed_origins"][0]}
        )
        assert response.status_code == 200
        assert all(
            all(flag in cookie.lower() for flag in ("secure", "httponly", "samesite=lax"))
            for cookie in response.headers.get_list("set-cookie")
        )
        assert "maya-token" not in response.text
