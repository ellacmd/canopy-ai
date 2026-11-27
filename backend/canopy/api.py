import secrets
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from canopy.admission import Admission
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
from canopy.models import AdmissionCounter, Customer, Order
from canopy.operations_api import router as operations_router
from canopy.order_views import OrderDetail, OrderSummary, Tracking, order_view
from canopy.policy_api import router as policy_router
from canopy.support_api import customer_tools
from canopy.support_api import router as support_router
from canopy.tools import SupportTools
from canopy.voice import router as voice_router
from canopy.workflow_api import router as workflow_router


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
    # A separate pool keeps admissions available while workflow transactions await providers.
    admission_engine = make_engine(settings) if engine.dialect.name == "postgresql" else engine
    admission = Admission(admission_engine, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            with httpx.Client(timeout=10, follow_redirects=False) as client:
                app.state.auth = SupabaseAuth(settings, client)
                yield
        finally:
            engine.dispose()
            if admission_engine is not engine:
                admission_engine.dispose()

    production = settings.environment == "production"
    app = FastAPI(
        title="Canopy AI",
        version="0.4.0",
        lifespan=lifespan,
        openapi_url=None if production else "/openapi.json",
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.admission = admission

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, error: RequestValidationError):
        # FastAPI's default validation payload can echo passwords or other sensitive input.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {
                        "loc": list(
                            item["loc"][:1] if item["type"] == "extra_forbidden" else item["loc"]
                        ),
                        "msg": "Invalid value" if item["type"] == "value_error" else item["msg"],
                        "type": item["type"],
                    }
                    for item in error.errors()
                ]
            },
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_failure(_request: Request, _error: SQLAlchemyError):
        return JSONResponse({"detail": "Database is temporarily unavailable"}, status_code=503)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "Idempotency-Key", "X-Request-ID"],
        expose_headers=["Retry-After", "X-Request-ID"],
    )

    @app.middleware("http")
    async def request_guard(request: Request, call_next):
        # Origin checking also protects cookie-based login, refresh, and future mutations.
        rejection = None
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and origin not in settings.allowed_origins:
                rejection = "Origin is not allowed"
            elif request.headers.get("sec-fetch-site") == "cross-site":
                rejection = "Cross-site requests are not allowed"
        response = (
            JSONResponse({"detail": rejection}, status_code=403)
            if rejection
            else await call_next(request)
        )
        response.headers["X-Request-ID"] = secrets.token_hex(16)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health/live", tags=["health"])
    def live():
        return {"status": "ok", "service": "canopy", "phase": 6}

    @app.get("/health/ready", tags=["health"])
    def ready(db: Session = Depends(get_db)):
        try:
            revisions = db.scalars(text("SELECT version_num FROM alembic_version")).all()
            if revisions != ["0008"]:
                raise HTTPException(503, "Database migrations are not current")
            db.execute(select(Customer.id).limit(1))
            with admission_engine.connect() as connection:
                connection.execute(select(AdmissionCounter.used).limit(1))
                if (
                    connection.dialect.name == "postgresql"
                    and connection.scalar(text("SELECT current_user")) != "service_role"
                ):
                    raise HTTPException(503, "Database runtime role is unavailable")
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
        admission.login(request.client.host if request.client else "unknown", str(body.email))
        token = auth.login(body)
        # Verify against Auth before setting a session or trusting the role.
        principal = auth.verify(token.access_token.get_secret_value())
        set_session(response, token, settings)
        return principal

    @app.post("/api/auth/refresh", tags=["auth"])
    def refresh(request: Request, response: Response, auth: SupabaseAuth = Depends(get_auth)):
        admission.refresh(request.client.host if request.client else "unknown")
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
    app.include_router(voice_router)
    app.include_router(operations_router)
    return app
