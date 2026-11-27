"""Database-clock request limits shared by every process and replica."""

import hmac
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, text
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from canopy.config import Settings
from canopy.models import AdmissionCounter

MINUTE = 60
DAY = 86_400


def database_epoch(connection: Connection) -> int:
    sql = (
        "SELECT floor(extract(epoch FROM statement_timestamp()))"
        if connection.dialect.name == "postgresql"
        else "SELECT strftime('%s', 'now')"
    )
    return int(connection.scalar(text(sql)))


@dataclass(frozen=True)
class Limit:
    name: str
    subject: str
    maximum: int
    seconds: int = MINUTE


class LimitExceeded(HTTPException):
    def __init__(self, seconds: int, *, daily_budget: bool):
        self.code = "daily_usage_budget" if daily_budget else "rate_limit"
        self.retry_after = seconds
        message = (
            "The daily AI usage budget has been reached. Try again after 00:00 UTC."
            if daily_budget
            else "Too many requests. Try again shortly."
        )
        super().__init__(429, message, headers={"Retry-After": str(seconds)})


class Admission:
    def __init__(self, engine: Engine, settings: Settings):
        self.engine, self.settings = engine, settings
        self.key = settings.admission_key.get_secret_value().encode()

    def login(self, ip: str, email: str) -> None:
        self.take(
            Limit("login.total", "", self.settings.login_attempts_total_minute),
            Limit("login.ip", ip, self.settings.login_attempts_per_ip_minute),
            Limit(
                "login.account", email.casefold(), self.settings.login_attempts_per_account_minute
            ),
        )

    def refresh(self, ip: str) -> None:
        self.take(
            Limit("refresh.total", "", self.settings.refresh_attempts_total_minute),
            Limit("refresh.ip", ip, self.settings.refresh_attempts_per_ip_minute),
        )

    def turn(self, actor: UUID) -> None:
        self.take(Limit("turn.actor", str(actor), self.settings.customer_turns_per_minute))

    def provider(self, operation: Literal["chat", "embedding"], actor: UUID) -> None:
        maximum = (
            self.settings.gemini_chat_calls_per_day
            if operation == "chat"
            else self.settings.gemini_embedding_calls_per_day
        )
        self.take(
            Limit(f"gemini.{operation}", "", maximum, DAY),
            Limit("gemini.actor", str(actor), self.settings.provider_calls_per_account_minute),
        )

    def take(self, *limits: Limit) -> None:
        table = AdmissionCounter.__table__
        try:
            # One independent commit before an upstream call. Failures and rollbacks still
            # consume an attempt. Deterministic ordering prevents cross-worker lock cycles.
            with self.engine.begin() as connection:
                now = database_epoch(connection)
                insert = (
                    postgres_insert if connection.dialect.name == "postgresql" else sqlite_insert
                )
                for limit in sorted(limits, key=lambda item: (item.name, item.subject)):
                    expiry = now + limit.seconds - now % limit.seconds
                    if limit.maximum == 0:
                        raise LimitExceeded(expiry - now, daily_budget=limit.seconds == DAY)
                    bucket = hmac.digest(
                        self.key if limit.subject else b"",
                        f"{limit.name}\0{limit.subject}".encode(),
                        "sha256",
                    ).hex()
                    reserve = insert(table).values(bucket=bucket, expires_at=expiry, used=1)
                    reserved = connection.scalar(
                        reserve.on_conflict_do_update(
                            index_elements=[table.c.bucket, table.c.expires_at],
                            set_={"used": table.c.used + 1},
                            where=table.c.used < limit.maximum,
                        ).returning(table.c.used)
                    )
                    if reserved is None:
                        raise LimitExceeded(expiry - now, daily_budget=limit.seconds == DAY)
        except SQLAlchemyError:
            raise HTTPException(503, "Request limits are temporarily unavailable") from None

    def prune(self) -> int:
        table = AdmissionCounter.__table__
        with self.engine.begin() as connection:
            # Keep a full day of expiry overlap so cleanup cannot race a delayed reservation.
            cutoff = database_epoch(connection) - DAY
            return connection.execute(delete(table).where(table.c.expires_at <= cutoff)).rowcount
