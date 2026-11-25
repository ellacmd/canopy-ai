import secrets
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from threading import Lock
from time import monotonic
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from canopy.auth import (
    LoginRequest,
    Principal,
    SupabaseAuth,
    TokenResponse,
    current_customer,
    current_principal,
    get_auth,
    require_roles,
)
from canopy.config import Settings
from canopy.db import get_db, make_engine
from canopy.models import Customer, Order, OrderItem, Product, Shipment
from canopy.policy_api import router as policy_router
from canopy.support_api import customer_tools
from canopy.support_api import router as support_router
from canopy.tools import SupportTools
from canopy.workflow_api import router as workflow_router


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


class LoginLimiter:
    """Process-local development protection. Replace with shared limits before release."""

    def __init__(self):
        self.events: dict[str, deque] = defaultdict(deque)
        self.lock = Lock()

    def check(self, key: str) -> None:
        with self.lock:
            now = monotonic()
            attempts = self.events[key]
            while attempts and attempts[0] < now - 60:
                attempts.popleft()
            if len(attempts) >= 10:
                raise HTTPException(429, "Too many attempts; try again in a minute")
            attempts.append(now)
            if len(self.events) > 10000:
                self.events = defaultdict(
                    deque, {k: v for k, v in self.events.items() if v and v[-1] >= now - 60}
                )


def set_session(response: Response, token: TokenResponse, settings: Settings) -> None:
    options = dict(httponly=True, secure=settings.environment == "production", samesite="lax")
    response.set_cookie(
        "canopy_access",
        token.access_token.get_secret_value(),
        max_age=token.expires_in,
        path="/api",
        **options,
    )
    response.set_cookie(
        "canopy_refresh",
        token.refresh_token.get_secret_value(),
        max_age=60 * 60 * 24 * 7,
        path="/api/auth",
        **options,
    )
    response.headers["Cache-Control"] = "no-store"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = make_engine(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            app.state.auth = SupabaseAuth(settings, client)
            yield
        engine.dispose()

    app = FastAPI(title="Canopy AI", version="0.4.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    limiter = LoginLimiter()

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, error: RequestValidationError):
        # FastAPI's default validation payload can echo passwords or other sensitive input.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": list(item["loc"]), "msg": item["msg"], "type": item["type"]}
                    for item in error.errors()
                ]
            },
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Request-ID", "Idempotency-Key"],
    )

    @app.middleware("http")
    async def request_guard(request: Request, call_next):
        # Origin checking also protects cookie-based login, refresh, and future mutations.
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and origin not in settings.allowed_origins:
                return JSONResponse({"detail": "Origin is not allowed"}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse(
                    {"detail": "Cross-site requests are not allowed"}, status_code=403
                )
        response = await call_next(request)
        response.headers["X-Request-ID"] = secrets.token_hex(16)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health/live", tags=["health"])
    def live():
        return {"status": "ok", "service": "canopy", "phase": 4}

    @app.get("/health/ready", tags=["health"])
    def ready(db: Session = Depends(get_db)):
        try:
            revisions = db.scalars(text("SELECT version_num FROM alembic_version")).all()
            if revisions != ["0005"]:
                raise HTTPException(503, "Database migrations are not current")
            db.execute(select(Customer.id).limit(1))
        except SQLAlchemyError:
            raise HTTPException(503, "Database is unavailable or migrations have not run") from None
        return {"status": "ok", "database": "ready"}

    @app.post("/api/auth/login", tags=["auth"])
    def login(
        body: LoginRequest,
        request: Request,
        response: Response,
        auth: SupabaseAuth = Depends(get_auth),
    ):
        limiter.check(request.client.host if request.client else "unknown")
        token = auth.login(body)
        # Verify against Auth before setting a session or trusting the role.
        principal = auth.verify(token.access_token.get_secret_value())
        set_session(response, token, settings)
        return principal

    @app.post("/api/auth/refresh", tags=["auth"])
    def refresh(request: Request, response: Response, auth: SupabaseAuth = Depends(get_auth)):
        limiter.check(request.client.host if request.client else "unknown")
        token = request.cookies.get("canopy_refresh")
        if not token:
            raise HTTPException(401, "Sign in to continue")
        renewed = auth.refresh(token)
        principal = auth.verify(renewed.access_token.get_secret_value())
        set_session(response, renewed, settings)
        return principal

    @app.post("/api/auth/logout", tags=["auth"], status_code=204)
    def logout(request: Request, auth: SupabaseAuth = Depends(get_auth)):
        token = request.cookies.get("canopy_access")
        if token:
            try:
                auth.logout(token)
            except HTTPException as error:
                if error.status_code != 401:
                    raise
        result = Response(status_code=204)
        result.delete_cookie("canopy_access", path="/api")
        result.delete_cookie("canopy_refresh", path="/api/auth")
        return result

    @app.get("/api/auth/me", tags=["auth"], response_model=Principal)
    def me(principal: Principal = Depends(current_principal)):
        return principal

    @app.get("/api/orders", tags=["orders"], response_model=list[OrderSummary])
    def list_orders(
        customer: Customer = Depends(current_customer),
        db: Session = Depends(get_db),
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        return db.scalars(
            select(Order)
            .where(Order.customer_id == customer.id)
            .order_by(Order.created_at.desc(), Order.id)
            .limit(limit)
            .offset(offset)
        ).all()

    @app.get("/api/orders/{order_id}", tags=["orders"], response_model=OrderDetail)
    def order_detail(
        order_id: UUID,
        customer: Customer = Depends(current_customer),
        db: Session = Depends(get_db),
    ):
        order = db.scalar(
            select(Order).where(Order.id == order_id, Order.customer_id == customer.id)
        )
        if order is None:
            # Same response for an absent order and another customer's order.
            raise HTTPException(404, "Order not found")
        return order_view(order, db)

    @app.get("/api/orders/lookup/{order_number}", response_model=OrderDetail, tags=["orders"])
    def lookup_order(
        order_number: str = Path(min_length=1, max_length=40),
        tools: SupportTools = Depends(customer_tools),
    ):
        return order_view(tools.lookup_order(order_number), tools.db)

    @app.get("/api/orders/{order_id}/tracking", tags=["orders"])
    def tracking(order_id: UUID, tools: SupportTools = Depends(customer_tools)):
        order, shipment = tools.get_order_tracking(order_id)
        return {
            "order_id": order.id,
            "order_number": order.order_number,
            "fulfillment_status": order.status,
            "shipment": Tracking.model_validate(shipment) if shipment else None,
            "message": "Stored shipment information is available."
            if shipment
            else "Tracking information is not available yet.",
        }

    @app.get("/api/admin/summary", tags=["operations"])
    def summary(
        principal: Principal = Depends(require_roles("admin", "support")),
        db: Session = Depends(get_db),
    ):
        return {
            "customers": db.scalar(select(func.count()).select_from(Customer)),
            "orders": db.scalar(select(func.count()).select_from(Order)),
        }

    app.include_router(support_router)
    app.include_router(policy_router)
    app.include_router(workflow_router)
    return app
