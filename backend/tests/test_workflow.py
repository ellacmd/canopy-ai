import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_retrieval import SyntheticEmbedder

from canopy.checkpoints import TurnCheckpointSaver
from canopy.contracts import EligibilityInput
from canopy.llm import ModelResult, ModelUnavailable
from canopy.models import (
    AgentExecution,
    Conversation,
    Escalation,
    Order,
    OrderItem,
    RefundQuote,
    RefundRequest,
    WorkflowCheckpoint,
    WorkflowTurn,
)
from canopy.policy import POLICY_VERSION
from canopy.retrieval import index_corpus
from canopy.seed import seed_id
from canopy.workflow import MODEL, SupportWorkflow
from canopy.workflow_api import get_workflow
from canopy.workflow_contracts import Classification, Confirmation, TurnInput
from canopy.workflow_traces import export_trace

ACTOR = uuid4()
CUSTOMER = seed_id("customer", "maya")


class FakeModel:
    """Mechanical graph tests; real model quality is evaluated separately."""

    def __init__(self):
        self.calls = []
        self.bad = False

    def generate_structured(self, prompt, *, system, schema):
        data = json.loads(prompt)
        self.calls.append(schema.__name__)
        if schema is Classification:
            message = data["message"].lower()
            intent = "other"
            if "orders" in message:
                intent = "orders"
            if "status" in message or ("order" in message and "orders" not in message):
                intent = "order_status"
            if "track" in message or "where is it" in message:
                intent = "tracking"
            if any(w in message for w in ["policy", "gift card", "return window"]):
                intent = "policy"
            if "refund" in message:
                intent = "refund"
            if "refund status" in message:
                intent = "refund_status"
            if "human" in message:
                intent = "human"
            if message in {"yes", "yes please", "confirm"}:
                intent = "confirmation"
            number = re.search(r"AMZ-\d{4,10}", data["message"], re.I)
            result = {
                "intent": intent,
                "order_number": number[0].upper() if number else None,
                "policy_query": "Can I return a gift card?" if intent == "policy" else None,
            }
        else:
            result = {
                "opening": "Here is what I found.",
                "fact_ids": ["invented"] if self.bad else [f["id"] for f in data["facts"]][:6],
            }
        return ModelResult(json.dumps(result), MODEL, 20, 10)


@pytest.fixture
def workflow_setup(engine, settings):
    embedder = SyntheticEmbedder()
    with Session(engine) as db, db.begin():
        # This fixture owns a disposable SQLite database, never shared seed records.
        db.get(Order, seed_id("order", "AMZ-1024")).delivered_at = datetime.now(UTC) - timedelta(
            days=2
        )
        index_corpus(db, embedder)
        c = Conversation(customer_id=CUSTOMER)
        db.add(c)
        db.flush()
        conversation_id = c.id
    config = settings.model_copy(update={"workflow_enabled": True, "policy_search_enabled": True})
    model = FakeModel()

    def factory(customer_id=CUSTOMER):
        return SupportWorkflow(
            engine, config, customer_id=customer_id, actor_id=ACTOR, model=model, embedder=embedder
        )

    return conversation_id, factory, model


def selection(number="AMZ-1024", reason="change_of_mind"):
    return EligibilityInput(
        order_id=seed_id("order", number),
        items=[{"order_item_id": seed_id("item", number), "quantity": 1}],
        reason=reason,
    )


def invoke(setup, body, key=None):
    conversation_id, factory, _ = setup
    return factory().run(conversation_id, TurnInput.model_validate(body), key or str(uuid4()))


def confirmation(quote):
    return {
        "action": "confirm_refund",
        "confirmation": {
            "quote_id": str(quote.id),
            "confirmed": True,
            "policy_version": POLICY_VERSION,
        },
    }


def test_transient_provider_retry_preserves_accounting_and_committed_replay(
    workflow_setup, engine, monkeypatch
):
    monkeypatch.setattr("canopy.provider_retry.sleep", lambda _: None)
    _, factory, model = workflow_setup
    original = model.generate_structured
    attempts = 0

    def flaky(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ModelUnavailable("temporary", code="provider_http_503")
        return original(*args, **kwargs)

    monkeypatch.setattr(model, "generate_structured", flaky)
    key = str(uuid4())
    body = {"message": "Order status AMZ-1024"}
    result = invoke(workflow_setup, body, key)
    assert result.order["order_number"] == "AMZ-1024"
    assert invoke(workflow_setup, body, key).replayed
    assert attempts == 3  # Failed classification, successful classification, composition.
    with Session(engine) as db:
        execution = db.scalar(select(AgentExecution))
        assert execution.status == "succeeded"
        assert execution.details["model_calls"] == 3
        assert execution.details["provider_attempt_errors"] == ["provider_http_503"]
        assert execution.details["usage_complete"] is False
        assert execution.details["error_code"] is None
        assert db.scalar(select(func.count()).select_from(WorkflowTurn)) == 1


def test_multiturn_order_tracking_and_checkpoint_survive_new_workflow_instance(
    workflow_setup, engine
):
    first = invoke(workflow_setup, {"message": "Order status AMZ-1024"})
    second = invoke(workflow_setup, {"message": "Where is it?"})
    assert first.order["order_number"] == "AMZ-1024"
    assert second.intent == "tracking" and "tracking number AMZ-1024" in second.text
    with Session(engine) as db:
        checkpoint = db.scalar(select(WorkflowCheckpoint))
        assert checkpoint.snapshot["checkpoint"]["channel_values"]["order_number"] == "AMZ-1024"
        assert db.scalar(select(func.count()).select_from(WorkflowTurn)) == 2
        assert db.scalar(select(AgentExecution)).details["model_calls"] == 2


def test_confirmation_is_explicit_quote_scoped_atomic_and_idempotent(workflow_setup, engine):
    preview = invoke(workflow_setup, {"selection": selection().model_dump(mode="json")})
    assert preview.state == "awaiting_confirmation" and preview.quote.amount == "89.00"
    with Session(engine) as db:
        assert db.get(Order, selection().order_id).refund_reserved_amount == 0
    yes = invoke(workflow_setup, {"message": "yes"})
    assert yes.state == "awaiting_confirmation" and yes.refund is None
    body = confirmation(preview.quote)
    key = str(uuid4())
    confirmed = invoke(workflow_setup, body, key)
    assert confirmed.state == "submitted" and confirmed.refund["simulated"] is True
    assert "No payment has been transferred" in confirmed.text
    assert invoke(workflow_setup, body, key).replayed
    assert invoke(workflow_setup, body).refund["replayed"]
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(RefundRequest)) == 1
        assert db.scalar(select(RefundQuote)).status == "consumed"


@pytest.mark.parametrize(
    "change", ["expired", "amount", "payment", "policy", "cancelled", "changed_text"]
)
def test_stale_changed_or_cancelled_quotes_cannot_submit(workflow_setup, engine, change):
    preview = invoke(workflow_setup, {"selection": selection().model_dump(mode="json")})
    if change == "changed_text":
        invoke(workflow_setup, {"message": "Refund two units instead"})
    elif change == "cancelled":
        invoke(workflow_setup, {"action": "cancel_refund"})
    else:
        with Session(engine) as db, db.begin():
            q = db.get(RefundQuote, preview.quote.id)
            if change == "expired":
                q.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            if change == "policy":
                q.policy_version = "old"
            if change == "amount":
                db.get(OrderItem, selection().items[0].order_item_id).unit_price = Decimal("90")
                db.get(Order, selection().order_id).total_amount = Decimal("90")
            if change == "payment":
                db.get(Order, selection().order_id).payment_status = "refunded"
    with pytest.raises(HTTPException) as error:
        invoke(workflow_setup, confirmation(preview.quote))
    assert error.value.status_code == 409
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(RefundRequest)) == 0
        assert db.get(Order, selection().order_id).refund_reserved_amount == 0


def test_unknown_or_foreign_thread_order_and_quote_are_denied(workflow_setup, engine):
    conversation_id, factory, _ = workflow_setup
    with pytest.raises(HTTPException) as error:
        factory(seed_id("customer", "jordan")).run(
            conversation_id, TurnInput(message="orders"), str(uuid4())
        )
    assert error.value.status_code == 404
    result = invoke(workflow_setup, {"message": "Track AMZ-1027"})
    assert result.order is None and result.state == "needs_information"
    with pytest.raises(HTTPException) as error:
        invoke(workflow_setup, {"selection": selection("AMZ-1027").model_dump(mode="json")})
    assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        invoke(
            workflow_setup,
            {
                "action": "confirm_refund",
                "confirmation": {
                    "quote_id": str(uuid4()),
                    "confirmed": True,
                    "policy_version": POLICY_VERSION,
                },
            },
        )
    assert error.value.status_code == 404


def test_changed_turn_body_conflicts_and_closed_conversation_rejects(workflow_setup, engine):
    key = str(uuid4())
    invoke(workflow_setup, {"message": "orders"}, key)
    with pytest.raises(HTTPException) as error:
        invoke(workflow_setup, {"message": "track"}, key)
    assert error.value.status_code == 409
    with Session(engine) as db, db.begin():
        db.get(Conversation, workflow_setup[0]).status = "closed"
    with pytest.raises(HTTPException) as error:
        invoke(workflow_setup, {"message": "orders"})
    assert error.value.status_code == 409


def test_invalid_generation_rolls_back_checkpoint_and_records_safe_failure(workflow_setup, engine):
    workflow_setup[2].bad = True
    with pytest.raises(ModelUnavailable):
        invoke(workflow_setup, {"message": "orders"})
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(WorkflowTurn)) == 0
        assert db.scalar(select(func.count()).select_from(WorkflowCheckpoint)) == 0
        execution = db.scalar(select(AgentExecution))
        assert execution.status == "failed" and execution.input_tokens == 40
        assert "invented" not in json.dumps(execution.details)


def test_fault_after_financial_node_rolls_back_action_and_quote_consumption(
    workflow_setup, engine, monkeypatch
):
    preview = invoke(workflow_setup, {"selection": selection().model_dump(mode="json")})
    original = SupportWorkflow.respond

    def fail(self, state):
        if self.body.action == "confirm_refund":
            raise ModelUnavailable("synthetic failure")
        return original(self, state)

    monkeypatch.setattr(SupportWorkflow, "respond", fail)
    with pytest.raises(ModelUnavailable):
        invoke(workflow_setup, confirmation(preview.quote))
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(RefundRequest)) == 0
        assert db.get(RefundQuote, preview.quote.id).status == "pending"
        assert db.get(Order, selection().order_id).refund_reserved_amount == 0


def test_asking_for_a_person_creates_a_ticket_and_is_retry_safe(workflow_setup, engine):
    request = {"message": "I want a human"}
    key = str(uuid4())
    result = invoke(workflow_setup, request, key)
    assert result.state == "escalated" and result.escalation
    assert invoke(workflow_setup, request, key).replayed
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Escalation)) == 1


def test_policy_citations_missing_evidence_and_exception_review(workflow_setup):
    policy = invoke(workflow_setup, {"message": "Gift card policy"})
    assert policy.citations and "human review" in policy.text
    cid, factory, _ = workflow_setup
    workflow = factory()
    workflow.settings = workflow.settings.model_copy(update={"policy_search_enabled": False})
    result = workflow.run(cid, TurnInput(message="Gift card policy"), str(uuid4()))
    assert result.state == "needs_human_review" and not result.citations
    assert (
        invoke(
            workflow_setup, {"selection": selection(reason="damaged").model_dump(mode="json")}
        ).state
        == "needs_human_review"
    )


def test_sse_only_streams_validated_response_and_checks_history_ownership(client, workflow_setup):
    cid, factory, _ = workflow_setup
    client.app.dependency_overrides[get_workflow] = factory
    headers = {"Authorization": "Bearer maya-token", "Idempotency-Key": str(uuid4())}
    response = client.post(
        f"/api/conversations/{cid}/turns/stream", json={"message": "orders"}, headers=headers
    )
    assert (
        response.status_code == 200
        and "event: stage" in response.text
        and "event: completed" in response.text
    )
    assert "event: text_delta" in response.text and "channel_values" not in response.text
    headers["Authorization"] = "Bearer jordan-token"
    for path in ["messages", "executions"]:
        assert client.get(f"/api/conversations/{cid}/{path}", headers=headers).status_code == 404
    assert (
        client.post(
            f"/api/conversations/{cid}/turns/stream", json={"message": "orders"}, headers=headers
        ).status_code
        == 404
    )


def test_api_conversation_roles_and_workflow_opt_in(client):
    assert client.post("/api/conversations").status_code == 401
    assert (
        client.post(
            "/api/conversations", headers={"Authorization": "Bearer support-token"}
        ).status_code
        == 403
    )
    headers = {"Authorization": "Bearer maya-token", "Idempotency-Key": str(uuid4())}
    cid = client.post("/api/conversations", headers=headers).json()["id"]
    assert (
        client.post(
            f"/api/conversations/{cid}/turns", json={"message": "orders"}, headers=headers
        ).status_code
        == 503
    )


def test_stream_failure_never_emits_unvalidated_text(client, workflow_setup):
    cid, factory, model = workflow_setup
    model.bad = True
    client.app.dependency_overrides[get_workflow] = factory
    response = client.post(
        f"/api/conversations/{cid}/turns/stream",
        json={"message": "orders"},
        headers={"Authorization": "Bearer maya-token", "Idempotency-Key": str(uuid4())},
    )
    assert "event: error" in response.text
    assert "event: text_delta" not in response.text and "event: completed" not in response.text


def test_stream_disconnect_before_commit_rolls_back_and_releases_conversation(
    workflow_setup, engine
):
    cid, factory, _ = workflow_setup
    events = factory().events(cid, TurnInput(selection=selection()), str(uuid4()))
    for event, payload in events:
        if event == "stage" and payload["stage"] == "action":
            break
    events.close()
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(RefundQuote)) == 0
        assert db.scalar(select(func.count()).select_from(WorkflowCheckpoint)) == 0
    assert invoke(workflow_setup, {"selection": selection().model_dump(mode="json")}).quote


def test_ambient_langsmith_tracing_cannot_capture_raw_graph_state(workflow_setup, monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    create = Mock()
    monkeypatch.setattr("langsmith.client.Client.create_run", create)
    invoke(workflow_setup, {"message": "orders"})
    create.assert_not_called()


def test_trace_export_allowlists_metadata_and_is_optional(settings):
    config = settings.model_copy(update={"workflow_tracing_enabled": True})
    client = Mock()
    metadata = {
        "state": "ready",
        "input_tokens": 12,
        "message": "private",
        "customer_id": "private",
        "exception": "private",
    }
    assert export_trace(config, str(uuid4()), metadata, client=client) == "submitted"
    payload = client.create_run.call_args.kwargs
    assert payload["outputs"] == {"state": "ready", "input_tokens": 12}
    assert "private" not in json.dumps(payload, default=str)
    client.create_run.side_effect = RuntimeError("secret key")
    assert export_trace(config, str(uuid4()), metadata, client=client) == "failed"
    assert export_trace(settings, str(uuid4()), metadata) == "disabled"


def test_checkpoint_scope_and_json_only():
    saver = TurnCheckpointSaver("owned", None)
    with pytest.raises(ValueError):
        saver.get_tuple({"configurable": {"thread_id": "foreign"}})
    with pytest.raises(TypeError):
        saver.put({"configurable": {"thread_id": "owned"}}, {"opaque": object()}, {}, {})


@pytest.mark.parametrize("value", [1, "true", False])
def test_confirmation_never_coerces_model_or_textual_consent(value):
    with pytest.raises(ValidationError):
        Confirmation(quote_id=uuid4(), confirmed=value, policy_version=POLICY_VERSION)


def test_customer_reads_a_current_quote_and_hides_it_after_expiry(client, workflow_setup, engine):
    conversation, _, _ = workflow_setup
    preview = invoke(workflow_setup, {"selection": selection().model_dump(mode="json")})
    path = f"/api/conversations/{conversation}/quote"
    maya = {"Authorization": "Bearer maya-token"}
    jordan = {"Authorization": "Bearer jordan-token"}
    assert client.get(path, headers=jordan).status_code == 404
    assert client.get(path, headers=maya).json() == preview.quote.model_dump(mode="json")

    with Session(engine) as db, db.begin():
        db.get(RefundQuote, preview.quote.id).expires_at = datetime.now(UTC) - timedelta(seconds=1)

    assert client.get(path, headers=maya).json() is None
