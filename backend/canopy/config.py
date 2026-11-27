from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    environment: Literal["development", "test", "production"] = "development"
    database_url: SecretStr = SecretStr("sqlite:///./canopy-dev.db")
    supabase_url: str = ""
    supabase_publishable_key: SecretStr = SecretStr("")
    allowed_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = Field(default="gemini-3.5-flash-lite", min_length=1)
    gemini_max_output_tokens: int = Field(default=512, ge=64, le=2048)
    gemini_timeout_seconds: float = Field(default=20, ge=1, le=60)
    provider_max_attempts: int = Field(default=2, ge=1, le=3)
    workflow_provider_budget_seconds: float = Field(default=80, ge=1, le=80)
    gemini_max_input_characters: int = Field(default=12000, ge=100, le=50000)
    # Opt in after configuring provider terms suitable for customer-entered query text.
    # Public/synthetic CLI indexing and evaluation do not require this flag.
    policy_search_enabled: bool = False
    workflow_enabled: bool = False
    voice_enabled: bool = False
    gemini_live_model: str = Field(default="gemini-3.8-live", min_length=1)
    gemini_live_calls_per_day: int = Field(default=100, ge=0, le=100000)
    voice_max_call_seconds: int = Field(default=300, ge=30, le=480)
    workflow_tracing_enabled: bool = False
    langsmith_api_key: SecretStr = SecretStr("")
    admission_key: SecretStr = SecretStr("")
    login_attempts_per_account_minute: int = Field(default=10, ge=1, le=10000)
    login_attempts_per_ip_minute: int = Field(default=40, ge=1, le=10000)
    login_attempts_total_minute: int = Field(default=300, ge=1, le=100000)
    refresh_attempts_per_ip_minute: int = Field(default=90, ge=1, le=10000)
    refresh_attempts_total_minute: int = Field(default=900, ge=1, le=100000)
    customer_turns_per_minute: int = Field(default=15, ge=1, le=10000)
    provider_calls_per_account_minute: int = Field(default=16, ge=1, le=10000)
    gemini_chat_calls_per_day: int = Field(default=500, ge=0, le=1000000)
    gemini_embedding_calls_per_day: int = Field(default=250, ge=0, le=1000000)

    @model_validator(mode="after")
    def validate_connections(self):
        if bool(self.supabase_url) != bool(self.supabase_publishable_key.get_secret_value()):
            raise ValueError("Set both SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY, or neither")
        if self.supabase_url:
            parsed = urlparse(self.supabase_url)
            local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            if not parsed.hostname or (
                parsed.scheme != "https"
                and not (parsed.scheme == "http" and local and self.environment != "production")
            ):
                raise ValueError(
                    "SUPABASE_URL requires HTTPS (HTTP allowed only for local development)"
                )
            if (
                parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("SUPABASE_URL must be an HTTP(S) origin")
            self.supabase_url = self.supabase_url.rstrip("/")
        for origin in self.allowed_origins:
            parsed = urlparse(origin)
            if (
                not parsed.hostname
                or parsed.scheme not in {"http", "https"}
                or f"{parsed.scheme}://{parsed.netloc}" != origin
                or parsed.username is not None
                or parsed.password is not None
                or "*" in parsed.netloc
            ):
                raise ValueError("ALLOWED_ORIGINS must contain explicit HTTP(S) origins")
        if self.environment == "production":
            try:
                database = make_url(self.database_url.get_secret_value())
            except (ArgumentError, ValueError):
                raise ValueError("Production requires a valid PostgreSQL DATABASE_URL") from None
            if (
                database.drivername != "postgresql+psycopg"
                or not database.host
                or database.query.get("sslmode") != "verify-full"
            ):
                raise ValueError("Production requires PostgreSQL with sslmode=verify-full")
            if not self.supabase_url:
                raise ValueError("Production requires Supabase authentication")
            if not self.allowed_origins or any(
                urlparse(origin).scheme != "https" for origin in self.allowed_origins
            ):
                raise ValueError("Production ALLOWED_ORIGINS require HTTPS")
            if len(self.admission_key.get_secret_value()) < 32:
                raise ValueError(
                    "Production requires a shared ADMISSION_KEY of at least 32 characters"
                )
            if (self.workflow_enabled or self.policy_search_enabled or self.voice_enabled) and (
                not self.gemini_api_key.get_secret_value()
            ):
                raise ValueError("Enabled Gemini workflows require GEMINI_API_KEY")
            if self.workflow_tracing_enabled and (
                not self.workflow_enabled or not self.langsmith_api_key.get_secret_value()
            ):
                raise ValueError("Trace export requires WORKFLOW_ENABLED and LANGSMITH_API_KEY")
        return self

    @property
    def auth_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_publishable_key.get_secret_value())
