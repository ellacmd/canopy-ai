from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from conftest import MAYA_AUTH
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from canopy.contracts import RefundInput
from canopy.models import (
    AuditEvent,
    Conversation,
    Escalation,
    Order,
    OrderItem,
    RefundLine,
    RefundRequest,
)
from canopy.policy import POLICY_VERSION
from canopy.seed import seed_id
from canopy.tools import SupportTools


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 24, 12, tzinfo=UTC)

    monkeypatch.setattr("canopy.tools.datetime", FixedDateTime)


def headers(key=None, token="maya-token"):
    result = {"Authorization": f"Bearer {token}"}
    if key:
        result["Idempotency-Key"] = key
    return result


def body(number="AMZ-1024", quantity=1, reason="change_of_mind"):
    return {
        "order_id": str(seed_id("order", number)),
        "items": [{"order_item_id": str(seed_id("item", number)), "quantity": quantity}],
        "reason": reason,
    }


def submission(**kwargs):
    return {**body(**kwargs), "confirmed": True, "policy_version": POLICY_VERSION}


def test_order_number_lookup_tracking_and_ownership(client):
    order = client.get("/api/orders/lookup/amz-1024", headers=headers())
    assert order.status_code == 200
    assert order.json()["items"][0]["order_item_id"] == str(seed_id("item", "AMZ-1024"))
    assert client.get("/api/orders/lookup/AMZ-1027", headers=headers()).status_code == 404
    response = client.get(f"/api/orders/{seed_id('order', 'AMZ-1025')}/tracking", headers=headers())
    assert response.json()["shipment"]["tracking_number"] == "SYNTHETIC-AMZ-1025"
    response = client.get(f"/api/orders/{seed_id('order', 'AMZ-1028')}/tracking", headers=headers())
    assert response.json()["shipment"] is None
    assert (
        client.get(
            f"/api/orders/{seed_id('order', 'AMZ-1027')}/tracking", headers=headers()
        ).status_code
        == 404
    )


def test_eligibility_has_no_side_effects_and_returns_policy(client, engine):
    result = client.post("/api/refunds/eligibility", json=body(), headers=headers())
    assert result.status_code == 200
    assert result.json()["outcome"] == "eligible"
    assert result.json()["amount"] == "89.00"
    assert result.json()["policy_version"] == POLICY_VERSION
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(RefundRequest)) == 0
        assert db.get(Order, seed_id("order", "AMZ-1024")).refund_reserved_amount == 0


def test_admin_publishes_refund_rules_and_old_policy_cannot_submit(client):
    url = "/api/policies/returns"
    publication = {
        "expected_revision": 1,
        "rules": {
            "return_days": 45,
            "refund_percent": 50,
            "max_item_quantity": 1,
            "max_request_amount": "50.00",
            "guidance": {"returns/return-window": "Keep your order number handy."},
        },
    }
    assert (
        client.put(url, json=publication, headers=headers(token="support-token")).status_code == 403
    )
    admin = headers(token="admin-token")
    changed = client.put(url, json=publication, headers=admin)
    assert changed.status_code == 200, changed.text
    current = changed.json()
    assert current["revision"] == 2
    assert client.put(url, json=publication, headers=admin).status_code == 409
    assert [row["revision"] for row in client.get(f"{url}/history", headers=admin).json()] == [2, 1]
    decision = client.post("/api/refunds/eligibility", json=body(), headers=headers()).json()
    assert (decision["policy_version"], decision["amount"]) == (current["version"], "44.50")
    stale = client.post("/api/refunds", json=submission(), headers=headers("old-policy-refund-01"))
    assert stale.status_code == 409
    assert client.get("/api/refunds", headers=headers()).json() == []

    publication["expected_revision"] = 2
    publication["rules"]["max_request_amount"] = "25.00"
    assert client.put(url, json=publication, headers=admin).status_code == 200
    review = client.post("/api/refunds/eligibility", json=body(), headers=headers()).json()
    assert (review["outcome"], review["reason_codes"]) == ("human_review", ["amount_limit"])


def test_refund_is_simulated_confirmed_persisted_and_idempotent(client, engine):
    request = submission()
    first = client.post("/api/refunds", json=request, headers=headers("same-refund-key-001"))
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "submitted"
    assert first.json()["simulated"] is True
    assert first.json()["confirmed_at"]
    again = client.post("/api/refunds", json=request, headers=headers("same-refund-key-001"))
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"] and again.json()["replayed"] is True
    assert client.get(f"/api/refunds/{first.json()['id']}", headers=headers()).status_code == 200
    assert (
        client.get(
            f"/api/refunds/{first.json()['id']}", headers=headers(token="jordan-token")
        ).status_code
        == 404
    )
    assert len(client.get("/api/refunds", headers=headers()).json()) == 1
    assert client.get("/api/admin/refunds", headers=headers()).status_code == 403
    review = client.get("/api/admin/refunds", headers=headers(token="support-token"))
    assert review.status_code == 200 and review.json()[0]["id"] == first.json()["id"]
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(RefundRequest)) == 1
        assert db.scalar(select(func.count()).select_from(RefundLine)) == 1
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 1
        assert db.get(OrderItem, seed_id("item", "AMZ-1024")).refund_reserved_quantity == 1
        assert db.get(Order, seed_id("order", "AMZ-1024")).refund_reserved_amount == Decimal(
            "89.00"
        )


def test_new_key_cannot_refund_reserved_items_again(client):
    assert (
        client.post(
            "/api/refunds", json=submission(), headers=headers("first-refund-key-01")
        ).status_code
        == 201
    )
    response = client.post(
        "/api/refunds", json=submission(), headers=headers("second-refund-key-2")
    )
    assert response.status_code == 409
    assert response.json()["detail"]["reason_codes"] == ["quantity_unavailable"]


def test_same_key_different_payload_conflicts_and_different_customer_key_is_independent(client):
    key = "customer-scoped-key1"
    assert client.post("/api/refunds", json=submission(), headers=headers(key)).status_code == 201
    assert (
        client.post(
            "/api/refunds", json={**submission(), "details": "changed"}, headers=headers(key)
        ).status_code
        == 409
    )
    response = client.post(
        "/api/refunds", json=submission(number="AMZ-1027"), headers=headers(key, "jordan-token")
    )
    assert response.status_code == 201


def test_partial_returns_reserve_only_requested_quantity(client, engine):
    with Session(engine) as db, db.begin():
        db.get(Order, seed_id("order", "AMZ-1026")).delivered_at = datetime(
            2026, 11, 20, tzinfo=UTC
        )
    request = submission(number="AMZ-1026")
    # Deterministic idempotency tokens used only in this synthetic test.
    for key in ["partial-refund-key1", "partial-refund-key2"]:  # gitleaks:allow
        response = client.post("/api/refunds", json=request, headers=headers(key))
        assert response.status_code == 201, response.text
        assert response.json()["amount"] == "24.00"
    assert (
        client.post(
            "/api/refunds", json=request, headers=headers("partial-refund-key3")
        ).status_code
        == 409
    )
    with Session(engine) as db:
        assert db.get(Order, seed_id("order", "AMZ-1026")).refund_reserved_amount == Decimal(
            "48.00"
        )


@pytest.mark.parametrize("confirmed", [False, 1, "true", None])
def test_submission_requires_explicit_boolean_confirmation(client, confirmed):
    response = client.post(
        "/api/refunds",
        json={**submission(), "confirmed": confirmed},
        headers=headers("confirmation-key01"),
    )
    assert response.status_code == 422


def test_server_rejects_client_amount_customer_identity_or_stale_policy(client):
    for field, value in [("amount", "0.01"), ("customer_id", str(uuid4())), ("simulated", False)]:
        assert (
            client.post(
                "/api/refunds",
                json={**submission(), field: value},
                headers=headers("forbidden-fields01"),
            ).status_code
            == 422
        )
    response = client.post(
        "/api/refunds",
        json={**submission(), "policy_version": "old-policy"},
        headers=headers("stale-policy-key01"),
    )
    assert response.status_code == 409 and response.json()["detail"]["code"] == "policy_changed"


@pytest.mark.parametrize(
    "reason,number,code",
    [
        ("damaged", "AMZ-1024", "exception_review"),
        ("change_of_mind", "AMZ-1031", "excluded_product"),
        ("change_of_mind", "AMZ-1026", "return_window_expired"),
        ("change_of_mind", "AMZ-1028", "payment_not_refundable"),
    ],
)
def test_disallowed_requests_create_no_refund(client, engine, reason, number, code):
    response = client.post(
        "/api/refunds",
        json=submission(number=number, reason=reason),
        headers=headers("ineligible-request1"),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["reason_codes"] == [code]
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(RefundRequest)) == 0


def test_foreign_order_or_item_is_never_refundable(client):
    request = body(number="AMZ-1027")
    assert (
        client.post("/api/refunds/eligibility", json=request, headers=headers()).status_code == 404
    )
    request["order_id"] = str(seed_id("order", "AMZ-1024"))
    assert (
        client.post("/api/refunds/eligibility", json=request, headers=headers()).status_code == 404
    )


@pytest.mark.parametrize("quantity", [0, -1, 1.1, "1", True])
def test_invalid_quantities_and_duplicate_selections_are_rejected(client, quantity):
    assert (
        client.post(
            "/api/refunds/eligibility", json=body(quantity=quantity), headers=headers()
        ).status_code
        == 422
    )
    request = body()
    request["items"] *= 2
    assert (
        client.post("/api/refunds/eligibility", json=request, headers=headers()).status_code == 422
    )


def test_unexpected_failure_rolls_back_reservations_and_audit(engine, monkeypatch):
    with Session(engine) as db:
        tools = SupportTools(db, customer_id=seed_id("customer", "maya"), actor_user_id=MAYA_AUTH)

        def fail(*args):
            raise RuntimeError("synthetic write failure")

        monkeypatch.setattr(tools, "_audit", fail)
        with pytest.raises(RuntimeError):
            tools.create_refund_request(
                RefundInput.model_validate(submission()), "rollback-request01"
            )
        db.commit()  # A caller catching the error still cannot accidentally commit reservations.
        assert db.get(Order, seed_id("order", "AMZ-1024")).refund_reserved_amount == 0
        assert db.get(OrderItem, seed_id("item", "AMZ-1024")).refund_reserved_quantity == 0
        assert db.scalar(select(func.count()).select_from(RefundRequest)) == 0


def ticket(**changes):
    return {
        "reason": "refund_exception",
        "summary": "Please review my damaged headphones.",
        "order_id": str(seed_id("order", "AMZ-1024")),
        **changes,
    }


def test_human_escalation_creates_context_once_and_support_resolves(client, engine):
    request = ticket()
    first = client.post("/api/escalations", json=request, headers=headers("human-review-key01"))
    assert first.status_code == 201, first.text
    record = first.json()
    replay = client.post("/api/escalations", json=request, headers=headers("human-review-key01"))
    assert replay.status_code == 200 and replay.json()["id"] == record["id"]
    assert (
        client.get(
            f"/api/escalations/{record['id']}", headers=headers(token="jordan-token")
        ).status_code
        == 404
    )
    assert client.get("/api/admin/escalations", headers=headers()).status_code == 403
    assert (
        len(
            client.get(
                "/api/admin/escalations?status=open", headers=headers(token="support-token")
            ).json()
        )
        == 1
    )
    path = f"/api/admin/escalations/{record['id']}"
    assert client.patch(path, json={"status": "resolved"}, headers=headers()).status_code == 403
    response = client.patch(
        path,
        json={"status": "in_progress", "assign_to_me": True},
        headers=headers(token="support-token"),
    )
    assert response.status_code == 200 and response.json()["assigned_agent_id"]
    assert (
        client.patch(
            path,
            json={"status": "resolved", "resolution_note": "Reviewed by support."},
            headers=headers(token="support-token"),
        ).status_code
        == 200
    )
    assert (
        client.patch(
            path, json={"status": "in_progress"}, headers=headers(token="support-token")
        ).status_code
        == 409
    )
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Escalation)) == 1
        assert db.get(Conversation, UUID(record["conversation_id"])).status == "resolved"


def test_foreign_escalation_context_and_reused_key_are_rejected(client, engine):
    request = ticket(order_id=str(seed_id("order", "AMZ-1027")))
    assert (
        client.post(
            "/api/escalations", json=request, headers=headers("foreign-review-key1")
        ).status_code
        == 404
    )
    with Session(engine) as db, db.begin():
        conversation = Conversation(customer_id=seed_id("customer", "jordan"))
        db.add(conversation)
        db.flush()
        foreign_id = str(conversation.id)
    assert (
        client.post(
            "/api/escalations",
            json=ticket(conversation_id=foreign_id),
            headers=headers("foreign-review-key2"),
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/escalations", json=ticket(), headers=headers("same-ticket-key001")
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/escalations",
            json=ticket(summary="Different issue"),
            headers=headers("same-ticket-key001"),
        ).status_code
        == 409
    )
