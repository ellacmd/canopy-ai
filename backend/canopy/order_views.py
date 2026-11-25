from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from canopy.models import Order, OrderItem, Product, Shipment


class OrderSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    order_number: str
    status: str
    payment_status: str
    total_amount: Decimal
    currency: str
    created_at: datetime
    delivered_at: datetime | None


class OrderLine(BaseModel):
    order_item_id: UUID
    product_name: str
    sku: str
    quantity: int
    unit_price: Decimal
    remaining_refundable_quantity: int


class Tracking(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    carrier: str
    tracking_number: str
    status: str
    estimated_delivery: datetime | None


class OrderDetail(OrderSummary):
    items: list[OrderLine]
    shipment: Tracking | None


def order_view(order: Order, db: Session) -> OrderDetail:
    rows = db.execute(
        select(OrderItem, Product).join(Product).where(OrderItem.order_id == order.id)
    ).all()
    shipment = db.scalar(select(Shipment).where(Shipment.order_id == order.id))
    return OrderDetail(
        **OrderSummary.model_validate(order).model_dump(),
        items=[
            OrderLine(
                order_item_id=item.id,
                product_name=product.name,
                sku=product.sku,
                quantity=item.quantity,
                unit_price=item.unit_price,
                remaining_refundable_quantity=item.quantity - item.refund_reserved_quantity,
            )
            for item, product in rows
        ],
        shipment=Tracking.model_validate(shipment) if shipment else None,
    )
