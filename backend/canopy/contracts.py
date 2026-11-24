from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RefundReason = Literal["change_of_mind", "damaged", "wrong_item", "other"]
Outcome = Literal["eligible", "ineligible", "human_review"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RefundSelection(Input):
    order_item_id: UUID
    quantity: int = Field(ge=1, le=10000, strict=True)


class EligibilityInput(Input):
    order_id: UUID
    items: list[RefundSelection] = Field(min_length=1, max_length=50)
    reason: RefundReason
    details: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def unique_items(self):
        if len({item.order_item_id for item in self.items}) != len(self.items):
            raise ValueError("Each order item may appear only once")
        return self


class RefundInput(EligibilityInput):
    confirmed: Literal[True]
    policy_version: str = Field(min_length=1, max_length=40)

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit customer confirmation must be true")
        return value


class EligibilityLine(BaseModel):
    order_item_id: UUID
    requested_quantity: int
    remaining_quantity: int
    amount: Decimal


class EligibilityResult(BaseModel):
    order_id: UUID
    outcome: Outcome
    reason_codes: list[str]
    explanation: str
    policy_version: str
    amount: Decimal
    currency: str
    return_window_ends: date | None
    items: list[EligibilityLine]
    simulated: Literal[True] = True


class RefundLineView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    order_item_id: UUID
    quantity: int
    amount: Decimal


class RefundView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    order_id: UUID
    status: str
    amount: Decimal
    currency: str
    reason: str
    policy_version: str
    confirmed_at: datetime | None
    created_at: datetime
    simulated: Literal[True] = True
    items: list[RefundLineView]
    replayed: bool = False


class EscalationInput(Input):
    reason: Literal[
        "human_requested", "refund_exception", "unsupported_request", "missing_information"
    ]
    summary: str = Field(min_length=1, max_length=2000)
    order_id: UUID | None = None
    conversation_id: UUID | None = None


class EscalationUpdate(Input):
    status: Literal["in_progress", "resolved", "closed"]
    assign_to_me: bool = False
    resolution_note: str = Field(default="", max_length=2000)


class EscalationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    conversation_id: UUID
    order_id: UUID | None
    reason: str
    summary: str
    status: str
    assigned_agent_id: UUID | None
    resolution_note: str | None
    created_at: datetime
    updated_at: datetime
    replayed: bool = False
