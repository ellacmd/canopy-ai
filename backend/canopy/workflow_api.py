import json
from contextlib import closing
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from canopy.auth import Principal, current_customer, current_principal
from canopy.db import get_db
from canopy.embedding import EmbeddingUnavailable, GeminiEmbedder
from canopy.llm import GeminiModel, ModelUnavailable
from canopy.models import AgentExecution, Conversation, Customer, Message
from canopy.support_api import IdempotencyKey
from canopy.workflow import SupportWorkflow, owned_conversation
from canopy.workflow_contracts import TurnInput, TurnResult

router = APIRouter(prefix="/api/conversations", tags=["support workflow"])


def get_workflow(
    request: Request,
    customer: Customer = Depends(current_customer),
    principal: Principal = Depends(current_principal),
):
    settings = request.app.state.settings
    if not settings.workflow_enabled:
        raise HTTPException(503, "Support workflow is not enabled for this environment")
    model = embedder = None
    try:
        model = GeminiModel(settings)
        if settings.policy_search_enabled:
            embedder = GeminiEmbedder(settings)
        yield SupportWorkflow(
            request.app.state.engine,
            settings,
            customer_id=customer.id,
            actor_id=principal.id,
            model=model,
            embedder=embedder,
        )
    except (ModelUnavailable, EmbeddingUnavailable):
        raise HTTPException(503, "Support model service is unavailable") from None
    finally:
        if model is not None:
            model.close()
        if embedder is not None:
            embedder.close()


@router.post("", status_code=201)
def create_conversation(
    customer: Customer = Depends(current_customer), db: Session = Depends(get_db)
):
    record = Conversation(customer_id=customer.id)
    db.add(record)
    db.commit()
    return {"id": record.id, "status": record.status, "created_at": record.created_at}


@router.get("")
def conversations(
    customer: Customer = Depends(current_customer),
    db: Session = Depends(get_db),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    rows = db.scalars(
        select(Conversation)
        .where(Conversation.customer_id == customer.id)
        .order_by(Conversation.updated_at.desc(), Conversation.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [
        {
            "id": c.id,
            "status": c.status,
            "updated_at": c.updated_at,
            "primary_intent": c.primary_intent,
        }
        for c in rows
    ]


@router.get("/{conversation_id}/messages")
def messages(
    conversation_id: UUID,
    customer: Customer = Depends(current_customer),
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    owned_conversation(db, conversation_id, customer.id)
    rows = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [
        {"id": m.id, "role": m.role, "content": m.content, "created_at": m.created_at} for m in rows
    ]


@router.get("/{conversation_id}/executions")
def executions(
    conversation_id: UUID,
    customer: Customer = Depends(current_customer),
    db: Session = Depends(get_db),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    owned_conversation(db, conversation_id, customer.id)
    rows = db.scalars(
        select(AgentExecution)
        .where(AgentExecution.conversation_id == conversation_id)
        .order_by(AgentExecution.created_at.desc(), AgentExecution.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [
        {"id": e.id, "trace_id": e.trace_id, "status": e.status, "details": e.details} for e in rows
    ]


@router.post("/{conversation_id}/turns", response_model=TurnResult)
def turn(
    conversation_id: UUID,
    body: TurnInput,
    idempotency_key: IdempotencyKey,
    workflow: SupportWorkflow = Depends(get_workflow),
):
    try:
        return workflow.run(conversation_id, body, idempotency_key)
    except ModelUnavailable:
        raise HTTPException(503, "Support model response is unavailable or invalid") from None
    except SQLAlchemyError:
        raise HTTPException(
            503, "Support database is unavailable; retry with the same key"
        ) from None


def sse(event, payload):
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


@router.post("/{conversation_id}/turns/stream")
def stream_turn(
    conversation_id: UUID,
    body: TurnInput,
    idempotency_key: IdempotencyKey,
    customer: Customer = Depends(current_customer),
    db: Session = Depends(get_db),
    workflow: SupportWorkflow = Depends(get_workflow),
):
    owned_conversation(db, conversation_id, customer.id)

    def generate():
        try:
            with closing(workflow.events(conversation_id, body, idempotency_key)) as events:
                for event, payload in events:
                    if event == "completed":
                        for start in range(0, len(payload["text"]), 64):
                            yield sse("text_delta", {"text": payload["text"][start : start + 64]})
                    yield sse(event, payload)
        except HTTPException as error:
            yield sse("error", {"code": "request_rejected", "status": error.status_code})
        except (ModelUnavailable, SQLAlchemyError):
            yield sse("error", {"code": "workflow_unavailable", "status": 503})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
    )
