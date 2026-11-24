from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from canopy.auth import Principal, current_customer, current_principal, require_roles
from canopy.contracts import (
    EligibilityInput,
    EligibilityResult,
    EscalationInput,
    EscalationUpdate,
    EscalationView,
    RefundInput,
    RefundView,
)
from canopy.db import get_db
from canopy.models import Customer, Escalation, RefundRequest
from canopy.tools import SupportTools, update_escalation

router = APIRouter(prefix="/api")
IdempotencyKey = Annotated[
    str, Header(alias="Idempotency-Key", min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
]


def customer_tools(
    customer: Customer = Depends(current_customer),
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    return SupportTools(db, customer_id=customer.id, actor_user_id=principal.id)


@router.post("/refunds/eligibility", response_model=EligibilityResult, tags=["refunds"])
def eligibility(body: EligibilityInput, tools: SupportTools = Depends(customer_tools)):
    return tools.check_refund_eligibility(body)


@router.post("/refunds", response_model=RefundView, status_code=201, tags=["refunds"])
def create_refund(
    body: RefundInput,
    response: Response,
    idempotency_key: IdempotencyKey,
    tools: SupportTools = Depends(customer_tools),
):
    result = tools.create_refund_request(body, idempotency_key)
    if result.replayed:
        response.status_code = 200
    return result


@router.get("/refunds", response_model=list[RefundView], tags=["refunds"])
def list_refunds(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    tools: SupportTools = Depends(customer_tools),
):
    records = tools.db.scalars(
        select(RefundRequest)
        .where(RefundRequest.customer_id == tools.customer_id)
        .order_by(RefundRequest.created_at.desc(), RefundRequest.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [tools.get_refund_status(record.id) for record in records]


@router.get("/refunds/{refund_id}", response_model=RefundView, tags=["refunds"])
def refund_status(refund_id: UUID, tools: SupportTools = Depends(customer_tools)):
    return tools.get_refund_status(refund_id)


@router.post("/escalations", response_model=EscalationView, status_code=201, tags=["escalations"])
def create_escalation(
    body: EscalationInput,
    response: Response,
    idempotency_key: IdempotencyKey,
    tools: SupportTools = Depends(customer_tools),
):
    result = tools.create_escalation(body, idempotency_key)
    if result.replayed:
        response.status_code = 200
    return result


@router.get("/escalations/{escalation_id}", response_model=EscalationView, tags=["escalations"])
def escalation_status(escalation_id: UUID, tools: SupportTools = Depends(customer_tools)):
    record = tools.db.scalar(
        select(Escalation).where(
            Escalation.id == escalation_id, Escalation.customer_id == tools.customer_id
        )
    )
    if record is None:
        raise HTTPException(404, "Escalation not found")
    return record


@router.get("/admin/escalations", response_model=list[EscalationView], tags=["operations"])
def admin_escalations(
    status: Literal["open", "in_progress", "resolved", "closed"] | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(require_roles("support", "admin")),
    db: Session = Depends(get_db),
):
    query = select(Escalation)
    if status:
        query = query.where(Escalation.status == status)
    return db.scalars(
        query.order_by(Escalation.created_at.desc(), Escalation.id).limit(limit).offset(offset)
    ).all()


@router.patch(
    "/admin/escalations/{escalation_id}", response_model=EscalationView, tags=["operations"]
)
def admin_update_escalation(
    escalation_id: UUID,
    body: EscalationUpdate,
    principal: Principal = Depends(require_roles("support", "admin")),
    db: Session = Depends(get_db),
):
    return update_escalation(db, escalation_id, body, principal.id)


@router.get("/admin/refunds", response_model=list[RefundView], tags=["operations"])
def admin_refunds(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(require_roles("support", "admin")),
    db: Session = Depends(get_db),
):
    records = db.scalars(
        select(RefundRequest)
        .order_by(RefundRequest.created_at.desc(), RefundRequest.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [
        SupportTools(
            db, customer_id=record.customer_id, actor_user_id=principal.id
        ).get_refund_status(record.id)
        for record in records
    ]
