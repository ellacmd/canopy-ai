from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from canopy.contracts import Input
from canopy.corpus import Section, Source, load_corpus
from canopy.models import RefundPolicyRevision
from canopy.policy import RefundRules, policy_version


class PolicyPublication(Input):
    expected_revision: int = Field(ge=1)
    rules: RefundRules


class PublishedPolicy(Input):
    revision: int
    version: str
    rules: RefundRules
    published_at: datetime


def latest_policy(db: Session, *, confirming=False, publishing=False) -> PublishedPolicy:
    if confirming or publishing:
        # Confirmations share the first row's lock. Publishing requires an exclusive lock.
        db.scalar(
            select(RefundPolicyRevision.revision)
            .where(RefundPolicyRevision.revision == 1)
            .with_for_update(read=confirming)
        )
    record = db.scalar(
        select(RefundPolicyRevision).order_by(RefundPolicyRevision.revision.desc()).limit(1)
    )
    if record is None:
        raise HTTPException(503, "The return policy is temporarily unavailable.")
    return view(record)


def view(record: RefundPolicyRevision) -> PublishedPolicy:
    return PublishedPolicy(
        revision=record.revision,
        version=policy_version(record.revision),
        rules=RefundRules.model_validate(record.settings),
        published_at=record.published_at,
    )


def publish(db: Session, publication: PolicyPublication, actor_id: UUID) -> PublishedPolicy:
    current = latest_policy(db, publishing=True)
    conflict = "Someone published a newer policy. Reload the policy before saving your changes."
    if publication.expected_revision != current.revision:
        raise HTTPException(409, conflict)
    allowed = {
        f"{source.key}/{section.anchor}" for source in load_corpus() for section in source.sections
    }
    guidance = {
        key: text.strip() for key, text in publication.rules.guidance.items() if text.strip()
    }
    if any(
        key not in allowed or len(text.encode("utf-8")) > 1200 for key, text in guidance.items()
    ):
        raise HTTPException(422, "Use an existing policy section and keep its guidance concise.")
    rules = publication.rules.model_copy(update={"guidance": guidance})
    if rules == current.rules:
        return current
    record = RefundPolicyRevision(
        revision=current.revision + 1,
        settings=rules.model_dump(mode="json"),
        published_by=actor_id,
    )
    db.add(record)
    try:
        db.flush()
        result = view(record)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, conflict) from None
    return result


def published_section(
    source: Source, section: Section, policy: PublishedPolicy, *, for_staff=False
) -> str:
    if policy.revision == 1 and not for_staff:
        return section.text
    rules = policy.rules
    replacements = {
        "return-window": (
            f"Standard returns are available through day {rules.return_days} after delivery. "
            "The order must be paid and delivered, and the items must be available to return."
        ),
        "exact-paid-amounts": (
            f"A refund request uses {rules.refund_percent}% of the price actually paid "
            "for the selected items, rounded down to the nearest cent for each item line. "
            "The request must fit within the amount still available to refund."
            + (
                f" Requests above ${rules.max_request_amount:.2f} USD go to a specialist."
                if rules.max_request_amount is not None
                else ""
            )
        ),
        "partial-quantities-and-reservations": (
            "Customers can request a return of whole items up to the number still available "
            "from the original purchase. Availability changes only after a request is submitted."
            + (
                f" More than {rules.max_item_quantity} of the same item in one request "
                "needs a specialist review."
                if rules.max_item_quantity is not None
                else ""
            )
        ),
        "explicit-confirmation": (
            "The customer must confirm the quoted items and amount using the on-screen button. "
            "We check the current policy and order again before submitting the request."
        ),
        "payment-and-order-state": (
            "Standard returns are available for paid, delivered orders. Cancelled orders, "
            "returned orders, and payments already refunded are ineligible. If the delivery "
            "date or payment details are unclear, the support team can help."
        ),
        "excluded-items": "Gift cards and other excluded products need a support review.",
        "damage-and-exceptions": (
            "Canopy sends damaged items, wrong items, and unusual returns to support for review, "
            "even when the standard return window has closed."
        ),
        "discounts-tax-and-shipping-allocations": (
            "The price paid for the items must match the order total. A specialist checks any "
            "unclear discounts, taxes, or shipping charges."
        ),
        "simulation-and-physical-returns": (
            "You can submit a request before sending your items back. Canopy records the request "
            "for review. No payment is transferred here."
        ),
        "duplicate-submissions": (
            "Retrying the same submission returns the original request. Once an item is included "
            "in a submitted request, that quantity is unavailable for another request."
        ),
        "human-support-and-policy-authority": (
            "A specialist reviews returns Canopy cannot handle on its own. The support team "
            "receives the conversation and can check any linked order."
        ),
    }
    body = replacements.get(section.anchor)
    text = (
        f"# {source.title}\n\n## {section.heading}\n\n{body}" if body is not None else section.text
    )
    guidance = rules.guidance.get(f"{source.key}/{section.anchor}")
    return f"{text}\n\nStore guidance\n{guidance}" if guidance else text
