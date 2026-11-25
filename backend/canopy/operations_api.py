"""Read-side operations data for verified support and admin accounts."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from canopy.auth import Principal, require_roles
from canopy.contracts import EscalationView, RefundView
from canopy.conversation_views import (
    ExecutionView,
    MessageView,
    recent_executions,
    recent_messages,
)
from canopy.db import get_db
from canopy.models import AgentExecution, Customer, Escalation, Order, RefundRequest
from canopy.order_views import OrderDetail, order_view
from canopy.tools import SupportTools

router = APIRouter(prefix="/api/admin", tags=["operations"])
staff = require_roles("support", "admin")


class CustomerContext(BaseModel):
    name: str
    email: str


class CaseContext(BaseModel):
    ticket: EscalationView
    customer: CustomerContext
    order: OrderDetail | None
    messages: list[MessageView]
    executions: list[ExecutionView]


class RefundContext(BaseModel):
    refund: RefundView
    customer: CustomerContext
    order: OrderDetail


def customer_and_order(db: Session, customer_id: UUID, order_id: UUID | None):
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(404, "Customer context is unavailable")
    order = None
    if order_id is not None:
        record = db.scalar(
            select(Order).where(Order.id == order_id, Order.customer_id == customer_id)
        )
        if record is None:
            raise HTTPException(404, "Order context is unavailable")
        order = order_view(record, db)
    return CustomerContext(name=customer.name, email=customer.email), order


@router.get("/escalations/{ticket_id}/context", response_model=CaseContext)
def case_context(
    ticket_id: UUID, db: Session = Depends(get_db), principal: Principal = Depends(staff)
):
    ticket = db.get(Escalation, ticket_id)
    if ticket is None:
        raise HTTPException(404, "Ticket not found")
    customer, order = customer_and_order(db, ticket.customer_id, ticket.order_id)
    return CaseContext(
        ticket=EscalationView.model_validate(ticket),
        customer=customer,
        order=order,
        messages=recent_messages(db, ticket.conversation_id, limit=30),
        executions=recent_executions(db, conversation_id=ticket.conversation_id, limit=20),
    )


@router.get("/refunds/{refund_id}/context", response_model=RefundContext)
def refund_context(
    refund_id: UUID, db: Session = Depends(get_db), principal: Principal = Depends(staff)
):
    record = db.get(RefundRequest, refund_id)
    if record is None:
        raise HTTPException(404, "Refund request not found")
    customer, order = customer_and_order(db, record.customer_id, record.order_id)
    refund = SupportTools(
        db, customer_id=record.customer_id, actor_user_id=principal.id
    ).get_refund_status(refund_id)
    return RefundContext(refund=refund, customer=customer, order=order)


@router.get("/executions", response_model=list[ExecutionView])
def executions(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
    db: Session = Depends(get_db),
    principal: Principal = Depends(staff),
):
    return recent_executions(db, limit=limit, offset=offset)


@router.get("/queue-metrics")
def queue_metrics(db: Session = Depends(get_db), principal: Principal = Depends(staff)):
    def count(model, *filters):
        return db.scalar(select(func.count()).select_from(model).where(*filters))

    return {
        "active_tickets": count(Escalation, Escalation.status.in_(("open", "in_progress"))),
        "submitted_refunds": count(RefundRequest, RefundRequest.status == "submitted"),
        "executions": count(AgentExecution),
        "failed_executions": count(AgentExecution, AgentExecution.status == "failed"),
    }
