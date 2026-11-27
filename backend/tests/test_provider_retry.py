from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from canopy.llm import ModelUnavailable
from canopy.provider_retry import retry_provider


def invoke(operation, *, before=None, deadline=100):
    return retry_provider(
        operation,
        attempts=2,
        before_attempt=before or Mock(),
        timeout=20,
        deadline=deadline,
        deadline_error=lambda message: ModelUnavailable(message, code="provider_deadline"),
    )


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr("canopy.provider_retry.monotonic", lambda: 0)
    monkeypatch.setattr("canopy.provider_retry.sleep", lambda _: None)


def test_transient_failure_recovers_and_each_attempt_is_admitted():
    operation = Mock(side_effect=[ModelUnavailable("safe", code="provider_http_503"), "ok"])
    admit = Mock()
    assert invoke(operation, before=admit) == "ok"
    assert operation.call_count == admit.call_count == 2


@pytest.mark.parametrize(
    "code", ["invalid_output", "blocked", "truncated", "provider_http_403", "provider_unavailable"]
)
def test_permanent_or_unknown_errors_are_not_retried(code):
    operation = Mock(side_effect=ModelUnavailable("safe", code=code))
    with pytest.raises(ModelUnavailable):
        invoke(operation)
    assert operation.call_count == 1


def test_retry_cannot_bypass_exhausted_shared_quota():
    operation = Mock(side_effect=[ModelUnavailable("safe", code="rate_limited"), "ok"])
    admit = Mock(side_effect=[None, HTTPException(429, "budget exhausted")])
    with pytest.raises(HTTPException) as failure:
        invoke(operation, before=admit)
    assert failure.value.status_code == 429
    assert operation.call_count == 1 and admit.call_count == 2


def test_exhausted_retries_and_deadline_are_bounded():
    operation = Mock(side_effect=ModelUnavailable("safe", code="provider_ReadTimeout"))
    with pytest.raises(ModelUnavailable):
        invoke(operation)
    assert operation.call_count == 2
    operation.reset_mock()
    with pytest.raises(ModelUnavailable):
        invoke(operation, deadline=20)
    assert operation.call_count == 1
    operation.reset_mock()
    with pytest.raises(ModelUnavailable) as failure:
        invoke(operation, deadline=19)
    assert failure.value.code == "provider_deadline"
    operation.assert_not_called()
