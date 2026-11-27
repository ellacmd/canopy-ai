"""Authorized business operations shared by HTTP routes and future graph nodes."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from canopy.contracts import (
    EligibilityInput,
    EscalationInput,
    EscalationUpdate,
    EscalationView,
    RefundInput,
    RefundLineView,
    RefundView,
)
from canopy.models import (
    AuditEvent,
    Conversation,
    Customer,
    Escalation,
    Order,
    OrderItem,
    Product,
    RefundLine,
    RefundRequest,
    Shipment,
)
from canopy.policy import ItemFacts, OrderFacts, evaluate_refund
from canopy.published_policy import PublishedPolicy, latest_policy


def fingerprint(body) -> str:
    data = body.model_dump(mode="json", exclude={"confirmed", "policy_version"})
    if "items" in data:
        data["items"] = sorted(data["items"], key=lambda item: item["order_item_id"])
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


class SupportTools:
    def __init__(self, db: Session, *, customer_id: UUID, actor_user_id: UUID, commit: bool = True):
        # Both identifiers come from backend authentication, never model/customer payloads.
        self.db = db
        self.customer_id = customer_id
        self.actor_user_id = actor_user_id
        self.commit = commit

    def get_customer_orders(self, *, limit=20, offset=0):
        return self.db.scalars(
            select(Order)
            .where(Order.customer_id == self.customer_id)
            .order_by(Order.created_at.desc(), Order.id)
            .limit(limit)
            .offset(offset)
        ).all()

    def get_order_details(self, order_id: UUID, *, lock=False) -> Order:
        query = select(Order).where(Order.id == order_id, Order.customer_id == self.customer_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        order = self.db.scalar(query)
        if order is None:
            raise HTTPException(404, "Order not found")
        return order

    def lookup_order(self, order_number: str) -> Order:
        order = self.db.scalar(
            select(Order).where(
                Order.customer_id == self.customer_id,
                Order.order_number == order_number.upper().strip(),
            )
        )
        if order is None:
            raise HTTPException(404, "Order not found")
        return order

    def get_order_tracking(self, order_id: UUID):
        order = self.get_order_details(order_id)
        shipment = self.db.scalar(select(Shipment).where(Shipment.order_id == order.id))
        return order, shipment

    def check_refund_eligibility(
        self, body: EligibilityInput, *, at=None, order=None, policy: PublishedPolicy | None = None
    ):
        order = order or self.get_order_details(body.order_id)
        policy = policy or latest_policy(self.db)
        rows = self.db.execute(
            select(OrderItem, Product)
            .join(Product)
            .where(OrderItem.order_id == order.id)
            .execution_options(populate_existing=True)
        ).all()
        owned = {item.id for item, _ in rows}
        if not {item.order_item_id for item in body.items} <= owned:
            raise HTTPException(404, "Order item not found")
        facts = OrderFacts(
            id=order.id,
            status=order.status,
            payment_status=order.payment_status,
            total_amount=order.total_amount,
            reserved_amount=order.refund_reserved_amount,
            currency=order.currency,
            delivered_at=order.delivered_at,
        )
        return evaluate_refund(
            facts,
            [
                ItemFacts(
                    id=item.id,
                    quantity=item.quantity,
                    reserved_quantity=item.refund_reserved_quantity,
                    unit_paid=item.unit_price,
                    returnable=product.returnable,
                )
                for item, product in rows
            ],
            {item.order_item_id: item.quantity for item in body.items},
            body.reason,
            at=at or datetime.now(UTC),
            rules=policy.rules,
            version=policy.version,
        )

    def _refund_replay(self, key: str, body):
        record = self.db.scalar(
            select(RefundRequest).where(
                RefundRequest.customer_id == self.customer_id, RefundRequest.idempotency_key == key
            )
        )
        if record:
            if record.request_fingerprint != fingerprint(body):
                raise HTTPException(409, "This idempotency key was used for a different request")
            return self._refund_view(record, replayed=True)
        return None

    def _refund_view(self, record, *, replayed=False):
        order = self.get_order_details(record.order_id)
        lines = self.db.scalars(
            select(RefundLine)
            .where(RefundLine.refund_request_id == record.id)
            .order_by(RefundLine.order_item_id)
        ).all()
        return RefundView(
            id=record.id,
            order_id=record.order_id,
            status=record.status,
            amount=record.amount,
            currency=order.currency,
            reason=record.reason,
            policy_version=record.policy_version,
            confirmed_at=record.confirmed_at,
            created_at=record.created_at,
            simulated=record.simulated,
            items=[RefundLineView.model_validate(line) for line in lines],
            replayed=replayed,
        )

    def get_refund_status(self, refund_id: UUID):
        record = self.db.scalar(
            select(RefundRequest).where(
                RefundRequest.id == refund_id, RefundRequest.customer_id == self.customer_id
            )
        )
        if record is None:
            raise HTTPException(404, "Refund request not found")
        return self._refund_view(record)

    def _audit(self, event_type, record, details):
        self.db.add(
            AuditEvent(
                actor_user_id=self.actor_user_id,
                customer_id=self.customer_id,
                event_type=event_type,
                entity_id=record.id,
                details=details,
            )
        )

    def create_refund_request(self, body: RefundInput, key: str):
        try:
            existing = self._refund_replay(key, body)
            if existing:
                return existing
            order = self.get_order_details(body.order_id, lock=True)
            # Recheck after the order lock; another transaction may have just committed.
            existing = self._refund_replay(key, body)
            if existing:
                return existing
            policy = latest_policy(self.db, confirming=True)
            if body.policy_version != policy.version:
                raise HTTPException(
                    409, {"code": "policy_changed", "policy_version": policy.version}
                )
            decision = self.check_refund_eligibility(body, order=order, policy=policy)
            if decision.outcome != "eligible":
                raise HTTPException(409, decision.model_dump(mode="json"))
            for item in sorted(body.items, key=lambda item: str(item.order_item_id)):
                changed = self.db.execute(
                    update(OrderItem)
                    .where(
                        OrderItem.id == item.order_item_id,
                        OrderItem.order_id == order.id,
                        OrderItem.refund_reserved_quantity + item.quantity <= OrderItem.quantity,
                    )
                    .values(
                        refund_reserved_quantity=OrderItem.refund_reserved_quantity + item.quantity
                    )
                )
                if changed.rowcount != 1:
                    raise HTTPException(409, "Refund quantity changed; check eligibility again")
            changed = self.db.execute(
                update(Order)
                .where(
                    Order.id == order.id,
                    Order.customer_id == self.customer_id,
                    Order.refund_reserved_amount + decision.amount <= Order.total_amount,
                )
                .values(refund_reserved_amount=Order.refund_reserved_amount + decision.amount)
            )
            if changed.rowcount != 1:
                raise HTTPException(409, "Refund amount changed; check eligibility again")
            record = RefundRequest(
                order_id=order.id,
                customer_id=self.customer_id,
                status="submitted",
                amount=decision.amount,
                reason=body.reason,
                customer_details=body.details,
                eligibility_result=decision.model_dump(mode="json"),
                policy_version=policy.version,
                idempotency_key=key,
                request_fingerprint=fingerprint(body),
                confirmed_at=datetime.now(UTC),
                simulated=True,
            )
            self.db.add(record)
            self.db.flush()
            for line in decision.items:
                self.db.add(
                    RefundLine(
                        refund_request_id=record.id,
                        order_id=order.id,
                        order_item_id=line.order_item_id,
                        quantity=line.requested_quantity,
                        amount=line.amount,
                    )
                )
            self._audit(
                "refund_request_created",
                record,
                {
                    "policy_version": policy.version,
                    "amount": str(decision.amount),
                    "currency": order.currency,
                    "simulated": True,
                },
            )
            self.db.flush()
            result = self._refund_view(record)
            if self.commit:
                self.db.commit()
            return result
        except (IntegrityError, HTTPException) as error:
            self.db.rollback()
            if isinstance(error, IntegrityError) or error.status_code == 409:
                existing = self._refund_replay(key, body)
                if existing:
                    return existing
            if isinstance(error, HTTPException):
                raise
            raise HTTPException(
                409, "A concurrent or inconsistent refund request was rejected"
            ) from None
        except OperationalError:
            self.db.rollback()
            raise HTTPException(
                503, "Database temporarily unavailable; retry with the same idempotency key"
            ) from None
        except Exception:
            self.db.rollback()
            raise

    def _escalation_replay(self, key, body):
        record = self.db.scalar(
            select(Escalation).where(
                Escalation.customer_id == self.customer_id, Escalation.idempotency_key == key
            )
        )
        if record:
            if record.request_fingerprint != fingerprint(body):
                raise HTTPException(409, "This idempotency key was used for a different request")
            return EscalationView(
                **EscalationView.model_validate(record).model_dump(exclude={"replayed"}),
                replayed=True,
            )
        return None

    def create_escalation(self, body: EscalationInput, key: str):
        try:
            existing = self._escalation_replay(key, body)
            if existing:
                return existing
            # Serializes same-customer ticket creation on PostgreSQL.
            self.db.execute(
                select(Customer.id).where(Customer.id == self.customer_id).with_for_update()
            )
            existing = self._escalation_replay(key, body)
            if existing:
                return existing
            if body.order_id:
                self.get_order_details(body.order_id)
            if body.conversation_id:
                conversation = self.db.scalar(
                    select(Conversation)
                    .where(
                        Conversation.id == body.conversation_id,
                        Conversation.customer_id == self.customer_id,
                    )
                    .with_for_update()
                )
                if conversation is None:
                    raise HTTPException(404, "Conversation not found")
                if conversation.status == "closed":
                    raise HTTPException(409, "This conversation is closed")
            else:
                conversation = Conversation(
                    customer_id=self.customer_id, primary_intent="human_escalation"
                )
                self.db.add(conversation)
                self.db.flush()
            conversation.status = "escalated"
            conversation.updated_at = datetime.now(UTC)
            record = Escalation(
                customer_id=self.customer_id,
                conversation_id=conversation.id,
                order_id=body.order_id,
                reason=body.reason,
                summary=body.summary,
                status="open",
                idempotency_key=key,
                request_fingerprint=fingerprint(body),
            )
            self.db.add(record)
            self.db.flush()
            self._audit("escalation_created", record, {"reason": body.reason})
            result = EscalationView.model_validate(record)
            if self.commit:
                self.db.commit()
            return result
        except (IntegrityError, HTTPException) as error:
            self.db.rollback()
            if isinstance(error, IntegrityError) or error.status_code == 409:
                existing = self._escalation_replay(key, body)
                if existing:
                    return existing
            if isinstance(error, HTTPException):
                raise
            raise HTTPException(409, "A conflicting escalation was rejected") from None
        except OperationalError:
            self.db.rollback()
            raise HTTPException(
                503, "Database temporarily unavailable; retry with the same idempotency key"
            ) from None
        except Exception:
            self.db.rollback()
            raise


def update_escalation(db: Session, escalation_id: UUID, body: EscalationUpdate, actor_id: UUID):
    conversation_id = db.scalar(
        select(Escalation.conversation_id).where(Escalation.id == escalation_id)
    )
    if conversation_id is None:
        raise HTTPException(404, "Escalation not found")
    # Serialize tickets sharing a conversation before counting its remaining open work.
    conversation = db.scalar(
        select(Conversation).where(Conversation.id == conversation_id).with_for_update()
    )
    record = db.scalar(select(Escalation).where(Escalation.id == escalation_id).with_for_update())
    if record is None:
        raise HTTPException(404, "Escalation not found")
    allowed = {
        "open": {"in_progress", "resolved", "closed"},
        "in_progress": {"resolved", "closed"},
        "resolved": {"closed"},
        "closed": set(),
    }
    if body.status != record.status and body.status not in allowed[record.status]:
        raise HTTPException(409, "This escalation status transition is not allowed")
    previous = record.status
    record.status = body.status
    record.updated_at = datetime.now(UTC)
    if body.assign_to_me:
        record.assigned_agent_id = actor_id
    if body.resolution_note:
        record.resolution_note = body.resolution_note
    db.flush()
    open_count = db.scalar(
        select(func.count())
        .select_from(Escalation)
        .where(
            Escalation.conversation_id == record.conversation_id,
            Escalation.status.in_(["open", "in_progress"]),
        )
    )
    conversation.status = "escalated" if open_count else "resolved"
    conversation.updated_at = datetime.now(UTC)
    db.add(
        AuditEvent(
            actor_user_id=actor_id,
            customer_id=record.customer_id,
            entity_id=record.id,
            event_type="escalation_updated",
            details={"previous_status": previous, "status": record.status},
        )
    )
    result = EscalationView.model_validate(record)
    db.commit()
    return result
