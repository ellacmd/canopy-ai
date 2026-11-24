"""Reproducible synthetic data. Existing records and auth links are never overwritten."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from canopy.models import Customer, Order, OrderItem, Product, Shipment


def seed_id(kind: str, key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"https://demo.canopy.invalid/{kind}/{key}")


CUSTOMERS = [
    ("maya", "Maya Chen", "maya@amazora.example"),
    ("jordan", "Jordan Williams", "jordan@amazora.example"),
    ("amara", "Amara Okafor", "amara@amazora.example"),
]
PRODUCTS = [
    ("HEAD-01", "Aero Wireless Headphones", "electronics", True),
    ("BAG-01", "Everyday Backpack", "accessories", True),
    ("CUP-01", "Insulated Travel Cup", "home", True),
    ("GIFT-50", "Amazora Gift Card — $50", "gift_cards", False),
]
# Number, owner, product, price, quantity, status, payment, order age, delivered age.
SCENARIOS = [
    ("AMZ-1024", "maya", "HEAD-01", "89.00", 1, "delivered", "paid", 8, 5),
    ("AMZ-1025", "maya", "BAG-01", "59.00", 1, "shipped", "paid", 3, None),
    ("AMZ-1026", "maya", "CUP-01", "24.00", 2, "delivered", "paid", 50, 45),
    ("AMZ-1027", "jordan", "HEAD-01", "89.00", 1, "delivered", "paid", 7, 4),
    ("AMZ-1028", "maya", "CUP-01", "24.00", 1, "pending", "pending", 0, None),
    ("AMZ-1029", "jordan", "BAG-01", "59.00", 1, "cancelled", "refunded", 4, None),
    ("AMZ-1030", "amara", "CUP-01", "24.00", 1, "returned", "refunded", 20, 17),
    ("AMZ-1031", "maya", "GIFT-50", "50.00", 1, "delivered", "paid", 2, 2),
    ("AMZ-1032", "amara", "BAG-01", "59.00", 2, "processing", "paid", 1, None),
    ("AMZ-1033", "jordan", "CUP-01", "24.00", 1, "confirmed", "paid", 1, None),
    ("AMZ-1034", "maya", "HEAD-01", "89.00", 1, "delivered", "paid", 34, 30),
]


def seed_data(db: Session, as_of: date) -> dict[str, int]:
    anchor = datetime.combine(as_of, datetime.min.time(), tzinfo=UTC)
    for key, name, email in CUSTOMERS:
        customer_id = seed_id("customer", key)
        if db.get(Customer, customer_id) is None:
            db.add(Customer(id=customer_id, name=name, email=email, created_at=anchor))
    for sku, name, category, returnable in PRODUCTS:
        product_id = seed_id("product", sku)
        if db.get(Product, product_id) is None:
            db.add(
                Product(
                    id=product_id,
                    sku=sku,
                    name=name,
                    category=category,
                    returnable=returnable,
                    created_at=anchor,
                )
            )
    db.flush()
    for number, owner, sku, price, quantity, status, payment, age, delivered in SCENARIOS:
        order_id = seed_id("order", number)
        if db.get(Order, order_id) is not None:
            continue
        order = Order(
            id=order_id,
            customer_id=seed_id("customer", owner),
            order_number=number,
            status=status,
            payment_status=payment,
            total_amount=Decimal(price) * quantity,
            currency="USD",
            created_at=anchor - timedelta(days=age),
            delivered_at=anchor - timedelta(days=delivered) if delivered is not None else None,
        )
        db.add(order)
        db.flush()
        db.add(
            OrderItem(
                id=seed_id("item", number),
                order_id=order.id,
                product_id=seed_id("product", sku),
                quantity=quantity,
                unit_price=Decimal(price),
                created_at=order.created_at,
            )
        )
        if status in {"shipped", "delivered", "returned"}:
            db.add(
                Shipment(
                    id=seed_id("shipment", number),
                    order_id=order.id,
                    carrier="Demo Express",
                    tracking_number=f"SYNTHETIC-{number}",
                    status=status,
                    estimated_delivery=(anchor + timedelta(days=2))
                    if status == "shipped"
                    else order.delivered_at,
                    created_at=order.created_at,
                    updated_at=anchor,
                )
            )
    db.flush()
    return {
        "customers": db.scalar(select(func.count()).select_from(Customer)),
        "products": db.scalar(select(func.count()).select_from(Product)),
        "orders": db.scalar(select(func.count()).select_from(Order)),
    }
