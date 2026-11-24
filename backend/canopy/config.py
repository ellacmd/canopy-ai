from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
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
    gemini_max_input_characters: int = Field(default=12000, ge=100, le=50000)

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
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("SUPABASE_URL must not contain credentials, query, or fragment")
            self.supabase_url = self.supabase_url.rstrip("/")
        for origin in self.allowed_origins:
            parsed = urlparse(origin)
            if not parsed.hostname or parsed.scheme not in {"http", "https"} or origin == "*":
                raise ValueError("ALLOWED_ORIGINS must contain explicit HTTP(S) origins")
        if self.environment == "production":
            if not self.database_url.get_secret_value().startswith("postgresql+psycopg://"):
                raise ValueError("Production requires a postgresql+psycopg DATABASE_URL")
            if not self.supabase_url:
                raise ValueError("Production requires Supabase authentication")
            if any(urlparse(origin).scheme != "https" for origin in self.allowed_origins):
                raise ValueError("Production ALLOWED_ORIGINS require HTTPS")
        return self

    @property
    def auth_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_publishable_key.get_secret_value())
