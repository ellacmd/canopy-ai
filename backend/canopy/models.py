from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from canopy.db import Base


def now() -> datetime:
    return datetime.now(UTC)


class Record:
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Customer(Record, Base):
    __tablename__ = "customers"
    auth_user_id: Mapped[UUID | None] = mapped_column(Uuid, unique=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254), unique=True)


class Product(Record, Base):
    __tablename__ = "products"
    sku: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(80))
    returnable: Mapped[bool] = mapped_column(default=True)


class Order(Record, Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("id", "customer_id"),
        CheckConstraint("total_amount >= 0", name="positive_total"),
        CheckConstraint(
            "refund_reserved_amount >= 0 AND refund_reserved_amount <= total_amount",
            name="refund_reservation",
        ),
        CheckConstraint("length(currency) = 3", name="currency_code"),
        CheckConstraint(
            "status IN ('pending','confirmed','processing','shipped','delivered',"
            "'cancelled','returned')",
            name="status",
        ),
        CheckConstraint(
            "payment_status IN ('pending','paid','failed','refunded')", name="payment_status"
        ),
    )
    customer_id: Mapped[UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    order_number: Mapped[str] = mapped_column(String(40), unique=True)
    status: Mapped[str] = mapped_column(String(20))
    payment_status: Mapped[str] = mapped_column(String(20))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    refund_reserved_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00"), server_default="0"
    )
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrderItem(Record, Base):
    __tablename__ = "order_items"
    __table_args__ = (
        UniqueConstraint("id", "order_id"),
        CheckConstraint("quantity > 0", name="positive_quantity"),
        CheckConstraint("unit_price >= 0", name="positive_price"),
        CheckConstraint(
            "refund_reserved_quantity >= 0 AND refund_reserved_quantity <= quantity",
            name="refund_reservation",
        ),
    )
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), index=True)
    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int]
    refund_reserved_quantity: Mapped[int] = mapped_column(default=0, server_default="0")
    # This is the actual unit amount paid. Unallocated order discounts require human review.
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))


class Shipment(Record, Base):
    __tablename__ = "shipments"
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), unique=True)
    carrier: Mapped[str] = mapped_column(String(80))
    tracking_number: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30))
    estimated_delivery: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Conversation(Record, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("id", "customer_id"),
        CheckConstraint("status IN ('active','resolved','escalated','closed')", name="status"),
    )
    customer_id: Mapped[UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    primary_intent: Mapped[str | None] = mapped_column(String(40))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Message(Record, Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('customer','assistant','support','system')", name="role"),
    )
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)


class RefundRequest(Record, Base):
    __tablename__ = "refund_requests"
    __table_args__ = (
        UniqueConstraint("id", "order_id"),
        UniqueConstraint("customer_id", "idempotency_key"),
        ForeignKeyConstraint(
            ["order_id", "customer_id"],
            ["orders.id", "orders.customer_id"],
            name="fk_refund_requests_order_owner",
        ),
        CheckConstraint("amount > 0", name="positive_amount"),
        CheckConstraint("status IN ('submitted','approved','rejected','completed')", name="status"),
        CheckConstraint("simulated", name="simulation_only"),
    )
    order_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    customer_id: Mapped[UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="submitted")
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    reason: Mapped[str] = mapped_column(Text)
    eligibility_result: Mapped[dict] = mapped_column(JSON)
    policy_version: Mapped[str] = mapped_column(String(40))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    # Existing records may have no recorded confirmation or request fingerprint.
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    customer_details: Mapped[str] = mapped_column(Text, default="", server_default="")
    simulated: Mapped[bool] = mapped_column(default=True, server_default=true())


class RefundLine(Record, Base):
    __tablename__ = "refund_lines"
    __table_args__ = (
        UniqueConstraint("refund_request_id", "order_item_id"),
        ForeignKeyConstraint(
            ["refund_request_id", "order_id"],
            ["refund_requests.id", "refund_requests.order_id"],
            name="fk_refund_lines_request_order",
        ),
        ForeignKeyConstraint(
            ["order_item_id", "order_id"],
            ["order_items.id", "order_items.order_id"],
            name="fk_refund_lines_item_order",
        ),
        CheckConstraint("quantity > 0", name="positive_quantity"),
        CheckConstraint("amount > 0", name="positive_amount"),
    )
    refund_request_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    order_id: Mapped[UUID] = mapped_column(Uuid)
    order_item_id: Mapped[UUID] = mapped_column(Uuid)
    quantity: Mapped[int]
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))


class PolicyDocument(Record, Base):
    __tablename__ = "policy_documents"
    title: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(60))
    version: Mapped[str] = mapped_column(String(40))
    storage_path: Mapped[str] = mapped_column(Text)
    index_status: Mapped[str] = mapped_column(String(30), default="pending")


class Escalation(Record, Base):
    __tablename__ = "escalations"
    __table_args__ = (
        UniqueConstraint("customer_id", "idempotency_key"),
        ForeignKeyConstraint(
            ["conversation_id", "customer_id"],
            ["conversations.id", "conversations.customer_id"],
            name="fk_escalations_conversation_owner",
        ),
        ForeignKeyConstraint(
            ["order_id", "customer_id"],
            ["orders.id", "orders.customer_id"],
            name="fk_escalations_order_owner",
        ),
        CheckConstraint("status IN ('open','in_progress','resolved','closed')", name="status"),
    )
    customer_id: Mapped[UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    conversation_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    order_id: Mapped[UUID | None] = mapped_column(Uuid)
    assigned_agent_id: Mapped[UUID | None] = mapped_column(Uuid)
    reason: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")
    idempotency_key: Mapped[str | None] = mapped_column(String(100))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, server_default=func.now()
    )


class AuditEvent(Record, Base):
    __tablename__ = "audit_events"
    actor_user_id: Mapped[UUID] = mapped_column(Uuid)
    customer_id: Mapped[UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    details: Mapped[dict] = mapped_column(JSON)


class AgentExecution(Record, Base):
    __tablename__ = "agent_executions"
    __table_args__ = (
        CheckConstraint("latency_ms >= 0", name="positive_latency"),
        CheckConstraint("input_tokens >= 0 AND output_tokens >= 0", name="positive_tokens"),
        CheckConstraint("estimated_cost >= 0", name="positive_cost"),
    )
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"), index=True)
    message_id: Mapped[UUID] = mapped_column(ForeignKey("messages.id"))
    trace_id: Mapped[str] = mapped_column(String(100), unique=True)
    workflow_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))
    latency_ms: Mapped[int]
    input_tokens: Mapped[int]
    output_tokens: Mapped[int]
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(12, 8))
