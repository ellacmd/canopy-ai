from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from canopy.config import Settings
from canopy.db import get_db
from canopy.models import Customer

Role = Literal["customer", "support", "admin"]
bearer = HTTPBearer(auto_error=False)


class Principal(BaseModel):
    id: UUID
    email: str
    role: Role


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: SecretStr = Field(min_length=1)
    refresh_token: SecretStr = Field(min_length=1)
    expires_in: int = Field(ge=1, le=86400)


@dataclass
class SupabaseAuth:
    settings: Settings
    client: httpx.Client

    def _request(self, method: str, path: str, *, token: str | None = None, **kwargs) -> dict:
        if not self.settings.auth_configured:
            raise HTTPException(503, "Supabase authentication is not configured")
        headers = {"apikey": self.settings.supabase_publishable_key.get_secret_value()}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = self.client.request(
                method, f"{self.settings.supabase_url}/auth/v1/{path}", headers=headers, **kwargs
            )
        except httpx.RequestError:
            raise HTTPException(503, "Authentication service is unavailable") from None
        if response.status_code in {400, 401, 403, 422}:
            raise HTTPException(401, "Invalid credentials or expired session")
        if response.status_code == 429:
            raise HTTPException(429, "Too many authentication attempts; try again later")
        if not response.is_success:
            raise HTTPException(503, "Authentication service is unavailable")
        try:
            data = response.json() if response.content else {}
            if not isinstance(data, dict):
                raise ValueError("Expected an object")
            return data
        except ValueError:
            raise HTTPException(503, "Invalid authentication service response") from None

    def login(self, body: LoginRequest) -> TokenResponse:
        return self._tokens(
            self._request(
                "POST",
                "token?grant_type=password",
                json={"email": str(body.email), "password": body.password.get_secret_value()},
            )
        )

    def refresh(self, token: str) -> TokenResponse:
        return self._tokens(
            self._request("POST", "token?grant_type=refresh_token", json={"refresh_token": token})
        )

    @staticmethod
    def _tokens(data: dict) -> TokenResponse:
        try:
            return TokenResponse.model_validate(data)
        except ValidationError:
            raise HTTPException(503, "Invalid authentication service response") from None

    def verify(self, token: str) -> Principal:
        # Never derive permissions from an unverified decoded JWT or user_metadata.
        user = self._request("GET", "user", token=token)
        try:
            return Principal(
                id=user["id"],
                email=user.get("email", ""),
                role=(user.get("app_metadata") or {}).get("role", "customer"),
            )
        except (KeyError, TypeError, ValidationError):
            raise HTTPException(403, "This account does not have a supported role") from None

    def logout(self, token: str) -> None:
        self._request("POST", "logout?scope=local", token=token)


def get_auth(request: Request) -> SupabaseAuth:
    return request.app.state.auth


def current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    auth: SupabaseAuth = Depends(get_auth),
) -> Principal:
    token = credentials.credentials if credentials else request.cookies.get("canopy_access")
    if not token:
        raise HTTPException(401, "Sign in to continue", headers={"WWW-Authenticate": "Bearer"})
    return auth.verify(token)


def current_customer(
    principal: Principal = Depends(current_principal), db: Session = Depends(get_db)
) -> Customer:
    if principal.role != "customer":
        raise HTTPException(403, "This endpoint is for customer accounts")
    customer = db.scalar(select(Customer).where(Customer.auth_user_id == principal.id))
    if customer is None:
        raise HTTPException(403, "No customer profile is linked to this account")
    return customer


def require_roles(*roles: Role):
    def check(principal: Principal = Depends(current_principal)) -> Principal:
        if principal.role not in roles:
            raise HTTPException(403, "You do not have permission to perform this action")
        return principal

    return check
