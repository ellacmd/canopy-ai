from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from canopy.policy import POLICY_VERSION, ItemFacts, OrderFacts, evaluate_refund

AT = datetime(2026, 11, 24, 12, tzinfo=UTC)
ITEM = ItemFacts(uuid4(), 2, 0, Decimal("24.00"), True)
ORDER = OrderFacts(
    uuid4(), "delivered", "paid", Decimal("48.00"), Decimal("0.00"), "USD", AT - timedelta(days=5)
)


@pytest.mark.parametrize(
    "order_changes,item_changes,reason,outcome,code",
    [
        ({}, {}, "change_of_mind", "eligible", "standard_return"),
        (
            {"payment_status": "pending"},
            {},
            "change_of_mind",
            "ineligible",
            "payment_not_refundable",
        ),
        (
            {"payment_status": "failed"},
            {},
            "change_of_mind",
            "ineligible",
            "payment_not_refundable",
        ),
        (
            {"payment_status": "refunded"},
            {},
            "change_of_mind",
            "ineligible",
            "payment_not_refundable",
        ),
        ({"status": "cancelled"}, {}, "change_of_mind", "ineligible", "order_closed"),
        ({"status": "returned"}, {}, "change_of_mind", "ineligible", "order_closed"),
        ({"status": "shipped"}, {}, "change_of_mind", "human_review", "not_delivered"),
        ({"status": "processing"}, {}, "change_of_mind", "human_review", "not_delivered"),
        ({"delivered_at": None}, {}, "change_of_mind", "human_review", "delivery_unverified"),
        (
            {"delivered_at": AT + timedelta(seconds=1)},
            {},
            "change_of_mind",
            "human_review",
            "delivery_unverified",
        ),
        (
            {"delivered_at": AT - timedelta(days=31)},
            {},
            "change_of_mind",
            "ineligible",
            "return_window_expired",
        ),
        ({}, {"returnable": False}, "change_of_mind", "human_review", "excluded_product"),
        ({}, {}, "damaged", "human_review", "exception_review"),
        ({}, {}, "wrong_item", "human_review", "exception_review"),
        ({}, {}, "other", "human_review", "exception_review"),
        (
            {"delivered_at": AT - timedelta(days=90)},
            {},
            "damaged",
            "human_review",
            "exception_review",
        ),
        ({}, {"reserved_quantity": 2}, "change_of_mind", "ineligible", "quantity_unavailable"),
        (
            {"total_amount": Decimal("40.00")},
            {},
            "change_of_mind",
            "human_review",
            "amount_reconciliation",
        ),
        (
            {"total_amount": Decimal("55.00")},
            {},
            "change_of_mind",
            "human_review",
            "amount_reconciliation",
        ),
        (
            {"reserved_amount": Decimal("40.00")},
            {},
            "change_of_mind",
            "ineligible",
            "amount_unavailable",
        ),
        ({"currency": "EUR"}, {}, "change_of_mind", "human_review", "unsupported_currency"),
    ],
)
def test_policy_decisions(order_changes, item_changes, reason, outcome, code):
    decision = evaluate_refund(
        replace(ORDER, **order_changes),
        [replace(ITEM, **item_changes)],
        {ITEM.id: 1},
        reason,
        at=AT,
    )
    assert decision.outcome == outcome
    assert decision.reason_codes == [code]
    assert decision.policy_version == POLICY_VERSION
    assert decision.amount == (Decimal("24.00") if outcome == "eligible" else Decimal("0.00"))


def test_entire_thirtieth_utc_day_is_inclusive_and_next_day_is_excluded():
    delivered = datetime(2026, 10, 25, 0, 0, tzinfo=UTC)
    order = replace(ORDER, delivered_at=delivered)
    for at, outcome in [
        (datetime(2026, 11, 24, 23, 59, 59, 999999, tzinfo=UTC), "eligible"),
        (datetime(2026, 11, 25, 0, 0, tzinfo=UTC), "ineligible"),
    ]:
        assert (
            evaluate_refund(order, [ITEM], {ITEM.id: 1}, "change_of_mind", at=at).outcome == outcome
        )


def test_partial_quantity_uses_exact_paid_value_and_remaining_quantity():
    item = replace(ITEM, reserved_quantity=1, unit_paid=Decimal("0.10"))
    order = replace(ORDER, total_amount=Decimal("0.20"), reserved_amount=Decimal("0.10"))
    decision = evaluate_refund(order, [item], {item.id: 1}, "change_of_mind", at=AT)
    assert decision.amount == Decimal("0.10")
    assert decision.items[0].remaining_quantity == 1


def test_invalid_or_zero_quantity_cannot_be_approved():
    for items, expected in [
        ({uuid4(): 1}, "invalid_items"),
        ({}, "invalid_items"),
        ({ITEM.id: 0}, "invalid_quantity"),
    ]:
        assert evaluate_refund(ORDER, [ITEM], items, "change_of_mind", at=AT).reason_codes == [
            expected
        ]


def test_sqlite_naive_timestamps_are_interpreted_as_utc():
    decision = evaluate_refund(
        replace(ORDER, delivered_at=ORDER.delivered_at.replace(tzinfo=None)),
        [ITEM],
        {ITEM.id: 1},
        "change_of_mind",
        at=AT,
    )
    assert decision.outcome == "eligible"
