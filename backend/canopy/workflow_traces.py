"""Allowlisted execution metadata only. Never attach graph callbacks or raw state."""

from datetime import UTC, datetime

from langsmith import Client

from canopy.config import Settings

SAFE_FIELDS = {
    "error_code",
    "workflow_version",
    "intent",
    "state",
    "status",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "estimated_chat_cost_usd",
    "usage_complete",
    "model_calls",
    "embedding_calls",
    "stages",
    "model",
    "pricing_version",
}


def export_trace(settings: Settings, trace_id: str, metadata: dict, *, client=None) -> str:
    if not settings.workflow_tracing_enabled:
        return "disabled"
    if client is None and not settings.langsmith_api_key.get_secret_value():
        return "unconfigured"
    # Construct from scratch. No identity, content, exceptions, or environment metadata.
    safe = {key: value for key, value in metadata.items() if key in SAFE_FIELDS}
    own = client is None
    try:
        client = client or Client(
            api_url="https://api.smith.langchain.com",
            api_key=settings.langsmith_api_key.get_secret_value(),
            auto_batch_tracing=False,
            timeout_ms=2000,
            omit_traced_runtime_info=True,
        )
        client.create_run(
            id=trace_id,
            name="canopy-support-v1",
            run_type="chain",
            project_name="canopy-support",
            inputs={"workflow_version": "support-v1"},
            outputs=safe,
            start_time=datetime.now(UTC),
            end_time=datetime.now(UTC),
        )
        return "submitted"
    except Exception:
        # Observability must not undo an already committed customer action.
        return "failed"
    finally:
        if own and client is not None:
            try:
                client.close()
            except Exception:
                pass
