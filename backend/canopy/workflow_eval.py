"""Live synthetic evaluation in a separately configured disposable local PostgreSQL DB."""

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from importlib.resources import files
from math import ceil
from pathlib import Path
from time import perf_counter, sleep
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from canopy.config import Settings
from canopy.db import make_engine
from canopy.embedding import GeminiEmbedder
from canopy.llm import GeminiModel, ModelUnavailable
from canopy.models import AgentExecution, Conversation, Order, OrderItem, RefundRequest
from canopy.policy import POLICY_VERSION
from canopy.provider_retry import TRANSIENT_PROVIDER_CODES
from canopy.retrieval import index_corpus
from canopy.seed import seed_data, seed_id
from canopy.workflow import MODEL, PRICING_VERSION, SupportWorkflow
from canopy.workflow_contracts import TurnInput

TRANSIENT_MODEL_CODES = TRANSIENT_PROVIDER_CODES | {"provider_unavailable", "provider_deadline"}
SUITES = {"baseline": "workflow-v1.json", "release": "workflow-release-v1.json"}


def percentiles(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "samples": len(ordered),
        "p50": ordered[ceil(len(ordered) * 0.50) - 1] if ordered else None,
        "p95": ordered[ceil(len(ordered) * 0.95) - 1] if ordered else None,
    }


class PacedModel:
    """Evaluation-only pacing; runtime retry and admission policy remains in the workflow."""

    def __init__(self, model, interval):
        self.model, self.interval = model, interval
        self.last_started = 0
        self.waits = []
        self.latencies = []

    def generate_structured(self, *args, **kwargs):
        start = perf_counter()
        sleep(max(0, self.interval - (start - self.last_started)))
        self.waits.append((perf_counter() - start) * 1000)
        self.last_started = perf_counter()
        try:
            return self.model.generate_structured(*args, **kwargs)
        finally:
            self.latencies.append((perf_counter() - self.last_started) * 1000)


def evaluate_workflow(
    settings: Settings, *, interval: float = 4, max_attempts: int = 1, suite: str = "baseline"
) -> dict:
    url = os.getenv("WORKFLOW_EVAL_DATABASE_URL", "")
    parsed = make_url(url) if url else None
    if (
        parsed is None
        or parsed.drivername != "postgresql+psycopg"
        or parsed.host not in {"localhost", "127.0.0.1"}
        or not (parsed.database or "").startswith("canopy_workflow_eval")
        or url == settings.database_url.get_secret_value()
        or not 0 <= interval <= 60
        or max_attempts not in range(1, 5)
    ):
        raise ValueError(
            "Use a separate loopback WORKFLOW_EVAL_DATABASE_URL named canopy_workflow_eval*"
        )
    if settings.gemini_model != MODEL:
        raise ValueError("Workflow evaluation requires its selected Gemini model")
    configured = settings.model_copy(
        update={
            "database_url": type(settings.database_url)(url),
            "workflow_enabled": True,
            "policy_search_enabled": True,
            "workflow_tracing_enabled": False,
        }
    )
    # The admin explicitly supplies the dedicated test DB; never touch the active .env database.
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
    engine = make_engine(configured)
    model = GeminiModel(settings)
    paced = PacedModel(model, interval)
    embedder = GeminiEmbedder(settings)
    fixture = files("canopy").joinpath("evaluations", SUITES[suite]).read_bytes()
    cases = json.loads(fixture)
    customer = seed_id("customer", "maya")
    actor = uuid4()
    results = []
    try:
        with Session(engine) as db, db.begin():
            seed_data(db, datetime.now(UTC).date())
            index_corpus(db, embedder)
        for index, case in enumerate(cases):
            started = perf_counter()
            with Session(engine) as db, db.begin():
                conv = Conversation(customer_id=customer)
                db.add(conv)
                db.flush()
                cid = conv.id

            observations = []
            timings = []
            model_errors = []
            provider_start = len(paced.latencies)

            def run(
                body,
                key=None,
                cid=cid,
                timings=timings,
                observations=observations,
                model_errors=model_errors,
            ):
                start = perf_counter()
                waiting = len(paced.waits)
                turn_key = key or str(uuid4())
                try:
                    for attempt in range(max_attempts):
                        try:
                            result = SupportWorkflow(
                                engine,
                                configured,
                                customer_id=customer,
                                actor_id=actor,
                                model=paced,
                                embedder=embedder,
                            ).run(cid, TurnInput.model_validate(body), turn_key)
                            break
                        except ModelUnavailable as error:
                            model_errors.append(error.code)
                            transient = error.code in TRANSIENT_MODEL_CODES
                            if not transient or attempt + 1 == max_attempts:
                                raise
                            wait = perf_counter()
                            sleep(10)
                            paced.waits.append((perf_counter() - wait) * 1000)
                finally:
                    timings.append(
                        round((perf_counter() - start) * 1000 - sum(paced.waits[waiting:]), 1)
                    )
                observations.append(
                    {
                        "intent": result.intent,
                        "state": result.state,
                        "citation_count": len(result.citations),
                    }
                )
                return result

            checks = []
            error_code = None
            try:
                if "turns" in case:
                    for turn in case["turns"]:
                        result = run(turn["body"])
                        checks.extend(
                            [result.intent == turn["intent"], result.state == turn["state"]]
                        )
                        if turn.get("citation"):
                            checks.append(bool(result.citations))
                        if expected := turn.get("evidence"):
                            checks.append(
                                any(
                                    citation.source == expected["source"]
                                    and citation.locator.rsplit("#", 1)[-1] == expected["anchor"]
                                    and citation.source_version == "1.0"
                                    and citation.rules_version == POLICY_VERSION
                                    for citation in result.citations
                                )
                            )
                        if turn.get("order_number"):
                            checks.append(
                                result.order
                                and result.order["order_number"] == turn["order_number"]
                            )
                        checks.append(result.refund is None and result.escalation is None)
                        if turn.get("order_absent"):
                            checks.append(result.order is None)
                else:
                    oid, iid = uuid4(), uuid4()
                    with Session(engine) as db, db.begin():
                        db.add(
                            Order(
                                id=oid,
                                customer_id=customer,
                                order_number=f"AMZ-{8000 + index}",
                                status="delivered",
                                payment_status="paid",
                                total_amount=Decimal("89"),
                                currency="USD",
                                delivered_at=datetime.now(UTC) - timedelta(days=2),
                            )
                        )
                        db.flush()
                        db.add(
                            OrderItem(
                                id=iid,
                                order_id=oid,
                                product_id=seed_id("product", "HEAD-01"),
                                quantity=1,
                                unit_price=Decimal("89"),
                            )
                        )
                    preview = run(
                        {
                            "selection": {
                                "order_id": str(oid),
                                "items": [{"order_item_id": str(iid), "quantity": 1}],
                                "reason": "damaged"
                                if case["flow"] == "damage_escalate"
                                else "change_of_mind",
                            }
                        }
                    )
                    if case["flow"] == "damage_escalate":
                        checks.append(
                            preview.state == "needs_human_review" and preview.quote is None
                        )
                        checks.append(run({"action": "escalate"}).escalation is not None)
                    else:
                        checks.append(preview.state == "awaiting_confirmation")
                        confirm = {
                            "action": "confirm_refund",
                            "confirmation": {
                                "quote_id": str(preview.quote.id),
                                "confirmed": True,
                                "policy_version": POLICY_VERSION,
                            },
                        }
                        if case["flow"] == "refund_cancel":
                            checks.append(run({"action": "cancel_refund"}).state == "cancelled")
                            try:
                                run(confirm)
                                checks.append(False)
                            except HTTPException as error:
                                checks.append(error.status_code == 409)
                        elif case["flow"] == "chat_confirmation":
                            yes = run({"message": "yes"})
                            checks.append(
                                yes.state == "awaiting_confirmation" and yes.refund is None
                            )
                        else:
                            key = str(uuid4())
                            result = run(confirm, key)
                            checks.append(
                                result.refund
                                and result.refund["simulated"]
                                and result.state == "submitted"
                            )
                            checks.append(run(confirm, key).replayed)
                    with Session(engine) as db:
                        count = db.scalar(
                            select(func.count())
                            .select_from(RefundRequest)
                            .where(RefundRequest.order_id == oid)
                        )
                        checks.append(count == (1 if case["flow"] == "refund_submit" else 0))
            except ModelUnavailable as error:
                error_code = (
                    "provider_unavailable" if error.code in TRANSIENT_MODEL_CODES else error.code
                )
                checks.append(False)
            except Exception as error:
                error_code = (
                    "request_rejected"
                    if isinstance(error, HTTPException)
                    else "workflow_unavailable"
                )
                checks.append(False)
            elapsed = round((perf_counter() - started) * 1000, 1)
            with Session(engine) as db:
                executions = db.scalars(
                    select(AgentExecution).where(AgentExecution.conversation_id == cid)
                ).all()
                details = [e.details for e in executions]
                if (
                    error_code is None
                    and not all(checks)
                    and any(d.get("error_code") == "embedding_unavailable" for d in details)
                ):
                    error_code = "provider_unavailable"
                result = {
                    "id": case["id"],
                    "passed": bool(checks) and all(checks),
                    "error": error_code,
                    "elapsed_ms_including_interval": elapsed,
                    "latency_ms": [e.latency_ms for e in executions],
                    "input_tokens": sum(e.input_tokens for e in executions),
                    "output_tokens": sum(e.output_tokens for e in executions),
                    "model_calls": sum(d["model_calls"] for d in details),
                    "embedding_calls": sum(d["embedding_calls"] for d in details),
                    "usage_complete": all(d["usage_complete"] for d in details),
                    "model_errors_across_attempts": model_errors,
                    "provider_attempt_errors": [
                        code for d in details for code in d.get("provider_attempt_errors", [])
                    ],
                    "error_codes": [d.get("error_code") for d in details if d.get("error_code")],
                    "observed": observations,
                    "latency_ms_excluding_pacing": timings,
                    "provider_latency_ms": [round(v, 1) for v in paced.latencies[provider_start:]],
                    "estimated_chat_cost_usd": str(
                        sum((e.estimated_cost for e in executions), Decimal("0"))
                    ),
                }
                results.append(result)
                print(
                    json.dumps({"case": case["id"], "passed": result["passed"]}),
                    file=sys.stderr,
                    flush=True,
                )
        return {
            "suite": suite,
            "fixture_sha256": sha256(fixture).hexdigest(),
            "model": MODEL,
            "pricing_version": PRICING_VERSION,
            "cases": len(results),
            "maximum_turn_attempts": max_attempts,
            "provider_max_attempts": configured.provider_max_attempts,
            "provider_timeout_seconds": configured.gemini_timeout_seconds,
            "workflow_provider_budget_seconds": configured.workflow_provider_budget_seconds,
            "minimum_seconds_between_model_calls": interval,
            "passed": sum(row["passed"] for row in results),
            "provider_unavailable": sum(row["error"] == "provider_unavailable" for row in results),
            "cases_with_provider_attempt_errors": sum(
                any(
                    code in TRANSIENT_MODEL_CODES
                    for code in row["model_errors_across_attempts"] + row["provider_attempt_errors"]
                )
                for row in results
            ),
            "turn_latency_ms_excluding_pacing": percentiles(
                [value for row in results for value in row["latency_ms_excluding_pacing"]]
            ),
            "provider_latency_ms": percentiles(
                [value for row in results for value in row["provider_latency_ms"]]
            ),
            "estimated_chat_cost_usd": str(
                sum((Decimal(row["estimated_chat_cost_usd"]) for row in results), Decimal("0"))
            ),
            "incomplete_chat_usage_cases": sum(not row["usage_complete"] for row in results),
            "cost_scope": "Chat only; embeddings and failed unreported provider usage excluded",
            "results": results,
        }
    finally:
        model.close()
        embedder.close()
        engine.dispose()
