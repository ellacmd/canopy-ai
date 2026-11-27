"""Approved deterministic return policy. No database, provider, or LLM dependencies."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from uuid import UUID

from pydantic import Field

from canopy.contracts import EligibilityLine, EligibilityResult, Input, RefundReason

POLICY_VERSION = "amazora-returns-v1.0"
ZERO = Decimal("0.00")


class RefundRules(Input):
    return_days: int = Field(default=30, ge=1, le=365)
    refund_percent: int = Field(default=100, ge=1, le=100)
    max_item_quantity: int | None = Field(default=None, ge=1, le=10000)
    max_request_amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    guidance: dict[str, str] = Field(default_factory=dict, max_length=32)


DEFAULT_RULES = RefundRules()


def policy_version(revision: int) -> str:
    return POLICY_VERSION if revision == 1 else f"amazora-returns-v{revision}.0"


@dataclass(frozen=True)
class OrderFacts:
    id: UUID
    status: str
    payment_status: str
    total_amount: Decimal
    reserved_amount: Decimal
    currency: str
    delivered_at: datetime | None


@dataclass(frozen=True)
class ItemFacts:
    id: UUID
    quantity: int
    reserved_quantity: int
    unit_paid: Decimal
    returnable: bool


def utc(value: datetime) -> datetime:
    # SQLite drops timezone annotations; persisted application timestamps are always UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def evaluate_refund(
    order: OrderFacts,
    all_items: list[ItemFacts],
    requested: dict[UUID, int],
    reason: RefundReason,
    *,
    at: datetime,
    rules: RefundRules = DEFAULT_RULES,
    version: str = POLICY_VERSION,
) -> EligibilityResult:
    instant = utc(at)
    delivered = utc(order.delivered_at) if order.delivered_at else None
    window_end = delivered.date() + timedelta(days=rules.return_days) if delivered else None
    selected = [item for item in all_items if item.id in requested]
    lines = [
        EligibilityLine(
            order_item_id=item.id,
            requested_quantity=requested[item.id],
            remaining_quantity=max(0, item.quantity - item.reserved_quantity),
            amount=(item.unit_paid * requested[item.id] * rules.refund_percent / 100).quantize(
                Decimal("0.01"), rounding=ROUND_DOWN
            ),
        )
        for item in selected
    ]
    amount = sum((line.amount for line in lines), ZERO)

    def result(outcome, code, explanation):
        return EligibilityResult(
            order_id=order.id,
            outcome=outcome,
            reason_codes=[code],
            explanation=explanation,
            policy_version=version,
            amount=amount if outcome == "eligible" else ZERO,
            currency=order.currency,
            return_window_ends=window_end,
            items=lines,
        )

    if set(requested) != {item.id for item in selected} or not requested:
        return result(
            "ineligible", "invalid_items", "One or more items are unavailable for this order."
        )
    if any(qty < 1 for qty in requested.values()):
        return result("ineligible", "invalid_quantity", "Requested quantities must be positive.")
    if order.payment_status != "paid":
        return result(
            "ineligible", "payment_not_refundable", "A verified, unrefunded payment is required."
        )
    if order.currency != "USD":
        return result(
            "human_review",
            "unsupported_currency",
            "Support must review orders outside the store's USD policy.",
        )
    if order.status in {"cancelled", "returned"}:
        return result("ineligible", "order_closed", "This order is already cancelled or returned.")
    if order.status != "delivered":
        return result(
            "human_review",
            "not_delivered",
            "Use tracking or ask support to review this undelivered order.",
        )
    if delivered is None or delivered > instant:
        return result(
            "human_review", "delivery_unverified", "Support must verify the recorded delivery date."
        )
    if any(line.requested_quantity > line.remaining_quantity for line in lines):
        return result(
            "ineligible",
            "quantity_unavailable",
            "The request includes more items than are still available to return.",
        )
    if any(not item.returnable for item in selected):
        return result(
            "human_review", "excluded_product", "An excluded product requires human review."
        )
    if reason != "change_of_mind":
        return result(
            "human_review", "exception_review", "A specialist needs to review this return."
        )
    if instant.date() > window_end:
        return result(
            "ineligible",
            "return_window_expired",
            f"The {rules.return_days}-day return window has ended.",
        )
    item_total = sum((item.unit_paid * item.quantity for item in all_items), ZERO)
    if item_total != order.total_amount:
        return result(
            "human_review",
            "amount_reconciliation",
            "Support must reconcile item payments, discounts, tax, or shipping "
            "before determining an amount.",
        )
    if rules.max_item_quantity is not None and any(
        line.requested_quantity > rules.max_item_quantity for line in lines
    ):
        return result(
            "human_review", "quantity_limit", "A specialist needs to review a return of this size."
        )
    if rules.max_request_amount is not None and amount > rules.max_request_amount:
        return result(
            "human_review", "amount_limit", "A specialist needs to review a refund of this amount."
        )
    if (
        any(line.amount <= ZERO for line in lines)
        or amount > order.total_amount - order.reserved_amount
    ):
        return result(
            "ineligible",
            "amount_unavailable",
            "The requested value exceeds the remaining refundable amount.",
        )
    return result(
        "eligible",
        "standard_return",
        "These items are eligible for a refund request. Confirm the details to submit it.",
    )
