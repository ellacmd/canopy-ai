"""Bounded conversation read models for the customer and staff workspaces."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from canopy.models import AgentExecution, Message, WorkflowTurn
from canopy.retrieval import Citation

STAGES = {"classify", "context", "action", "respond"}
TRACE_STATES = {"pending", "disabled", "unconfigured", "submitted", "failed"}


class MessageView(BaseModel):
    id: UUID
    role: str
    content: str
    created_at: datetime
    citations: list[Citation]
    workflow: bool = False


class ExecutionView(BaseModel):
    id: UUID
    trace_id: str
    conversation_id: UUID
    status: str
    created_at: datetime
    latency_ms: int
    input_tokens: int
    output_tokens: int
    stages: list[str]
    trace_delivery: Literal["unknown", "pending", "disabled", "unconfigured", "submitted", "failed"]


def recent_messages(
    db: Session, conversation_id: UUID, *, limit: int, offset: int = 0
) -> list[MessageView]:
    records = list(
        db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    message_ids = [str(record.id) for record in records if record.role == "assistant"]
    citations = {}
    if message_ids:
        response_message = WorkflowTurn.response["message_id"].as_string()
        for response in db.scalars(
            select(WorkflowTurn.response).where(
                WorkflowTurn.conversation_id == conversation_id,
                response_message.in_(message_ids),
            )
        ):
            citations[response["message_id"]] = response.get("citations", [])
    return [
        MessageView(
            id=record.id,
            role=record.role,
            content=record.content,
            created_at=record.created_at,
            citations=citations.get(str(record.id), []),
            workflow=str(record.id) in citations,
        )
        for record in reversed(records)
    ]


def recent_executions(
    db: Session, *, limit: int, offset: int = 0, conversation_id: UUID | None = None
) -> list[ExecutionView]:
    query = select(AgentExecution)
    if conversation_id is not None:
        query = query.where(AgentExecution.conversation_id == conversation_id)
    records = db.scalars(
        query.order_by(AgentExecution.created_at.desc(), AgentExecution.id.desc())
        .limit(limit)
        .offset(offset)
    )
    result = []
    for record in records:
        trace_delivery = record.details.get("trace_delivery")
        view = ExecutionView(
            id=record.id,
            trace_id=record.trace_id,
            conversation_id=record.conversation_id,
            status=record.status,
            created_at=record.created_at,
            latency_ms=record.latency_ms,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            stages=[
                stage
                for stage in record.details.get("stages", [])
                if isinstance(stage, str) and stage in STAGES
            ],
            trace_delivery=trace_delivery
            if isinstance(trace_delivery, str) and trace_delivery in TRACE_STATES
            else "unknown",
        )
        result.append(view)
    return result
