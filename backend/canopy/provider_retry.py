"""Explicit, bounded retries; callers admit and account for every provider attempt."""

from random import uniform
from time import monotonic, sleep

TRANSIENT_PROVIDER_CODES = {
    "rate_limited",
    "provider_http_500",
    "provider_http_502",
    "provider_http_503",
    "provider_http_504",
    "provider_ReadTimeout",
    "provider_ConnectTimeout",
    "provider_ConnectError",
    "provider_RemoteProtocolError",
}


def provider_error_code(error):
    status = getattr(error, "code", None)
    if status == 429:
        return "rate_limited"
    if status in {400, 401, 403, 404, 500, 502, 503, 504}:
        return f"provider_http_{status}"
    name = type(error).__name__
    if name in {
        "ReadTimeout",
        "ConnectTimeout",
        "ConnectError",
        "RemoteProtocolError",
        "JSONDecodeError",
        "AttributeError",
        "TypeError",
        "ValidationError",
    }:
        return "provider_" + name
    return "provider_unavailable"


def retry_provider(operation, *, attempts, before_attempt, timeout, deadline, deadline_error):
    """Retry known transient failures only, within a shared monotonic turn deadline.

    SDK retries stay disabled. Admission errors, invalid output, and financial actions
    are never retried here. A whole attempt must fit before starting it.
    """
    for attempt in range(attempts):
        if monotonic() + timeout > deadline:
            raise deadline_error("The provider time budget for this request was exhausted")
        before_attempt()
        try:
            return operation()
        except Exception as error:
            delay = uniform(0.25, 0.5) * 2**attempt
            if (
                getattr(error, "code", None) not in TRANSIENT_PROVIDER_CODES
                or attempt + 1 == attempts
                or monotonic() + delay + timeout > deadline
            ):
                raise
            sleep(delay)
