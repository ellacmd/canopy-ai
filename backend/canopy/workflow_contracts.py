from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from canopy.contracts import EligibilityInput, Input
from canopy.retrieval import Citation

Intent = Literal[
    "orders",
    "order_status",
    "tracking",
    "policy",
    "refund",
    "refund_status",
    "human",
    "confirmation",
    "other",
]


class Classification(Input):
    intent: Intent
    order_number: str | None = Field(default=None, pattern=r"^AMZ-[0-9]{4,10}$")
    policy_query: str | None = Field(default=None, min_length=3, max_length=300)


class GroundedReply(Input):
    opening: Literal[
        "Here is what I found.", "I need more information.", "A support specialist can help."
    ]
    fact_ids: list[str] = Field(min_length=1, max_length=6)


class Confirmation(Input):
    quote_id: UUID
    confirmed: Literal[True]
    policy_version: str = Field(min_length=1, max_length=40)

    @model_validator(mode="before")
    @classmethod
    def explicit(cls, value):
        if isinstance(value, dict) and value.get("confirmed") is not True:
            raise ValueError("Explicit confirmed: true is required")
        return value


class TurnInput(Input):
    action: Literal["message", "confirm_refund", "cancel_refund", "escalate"] = "message"
    message: str = Field(default="", max_length=2000)
    selection: EligibilityInput | None = None
    confirmation: Confirmation | None = None

    @model_validator(mode="after")
    def valid_action(self):
        if self.action == "message" and not self.message and self.selection is None:
            raise ValueError("A message or explicit refund selection is required")
        if (self.action == "confirm_refund") != (self.confirmation is not None):
            raise ValueError("Confirmation belongs only to confirm_refund")
        if self.selection is not None and self.action != "message":
            raise ValueError("Selections require a preview before confirmation")
        if self.action != "message" and self.message:
            raise ValueError("Send changed instructions as a new message before confirming")
        return self


class QuoteView(Input):
    id: UUID
    amount: str
    currency: str
    policy_version: str
    expires_at: datetime
    selection: EligibilityInput


class TurnResult(Input):
    conversation_id: UUID
    turn_id: UUID
    message_id: UUID
    intent: str
    state: Literal[
        "ready",
        "awaiting_confirmation",
        "needs_information",
        "needs_human_review",
        "submitted",
        "escalated",
        "cancelled",
    ]
    text: str
    citations: list[Citation] = Field(default_factory=list)
    quote: QuoteView | None = None
    refund: dict | None = None
    escalation: dict | None = None
    order: dict | None = None
    eligibility: dict | None = None
    execution_id: UUID
    replayed: bool = False
