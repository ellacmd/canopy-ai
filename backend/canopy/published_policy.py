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


def published_section(source: Source, section: Section, policy: PublishedPolicy) -> str:
    if policy.revision == 1:
        return section.text
    rules = policy.rules
    replacements = {
        "return-window": (
            f"Standard returns are available through day {rules.return_days} after delivery. "
            "The order must be paid and delivered, and the items must be available to return."
        ),
        "exact-paid-amounts": (
            f"A simulated refund uses {rules.refund_percent}% of the price actually paid "
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
    }
    body = replacements.get(section.anchor)
    text = (
        f"# {source.title}\n\n## {section.heading}\n\n{body}" if body is not None else section.text
    )
    guidance = rules.guidance.get(f"{source.key}/{section.anchor}")
    return f"{text}\n\nStore guidance\n{guidance}" if guidance else text
