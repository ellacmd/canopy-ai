"""Verify an HTTPS release with pre-provisioned synthetic accounts."""

import argparse
import json
import os
import sys
from contextlib import ExitStack, suppress
from http.cookies import SimpleCookie
from urllib.parse import urlsplit
from uuid import uuid4

import httpx


class SmokeFailure(RuntimeError):
    pass


def expect(condition, check):
    if not condition:
        raise SmokeFailure(check)


def sign_in(client: httpx.Client, account: str, role: str):
    email = os.getenv(f"CANOPY_SMOKE_{account}_EMAIL")
    password = os.getenv(f"CANOPY_SMOKE_{account}_PASSWORD")
    expect(email and password, f"Configure synthetic {account.lower()} credentials")
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    expect(response.status_code == 200 and response.json().get("role") == role, "Sign-in and role")
    cookies = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        cookies.load(header)
    for name in ("canopy_access", "canopy_refresh"):
        cookie = cookies.get(name)
        expect(
            cookie
            and cookie["secure"]
            and cookie["httponly"]
            and cookie["samesite"].lower() == "lax",
            "Secure HttpOnly session cookies",
        )
    expect(
        all(token not in response.text for token in (c.value for c in cookies.values())),
        "Session token redaction",
    )


def sign_out(client: httpx.Client) -> None:
    with suppress(httpx.HTTPError):
        client.post("/api/auth/logout")


def committed_turn(client: httpx.Client, url: str, body: dict, headers: dict) -> dict:
    with client.stream("POST", f"{url}/stream", json=body, headers=headers) as result:
        expect(result.status_code == 200, "Live workflow stream")
        event = None
        for line in result.iter_lines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            elif line.startswith("data: ") and event == "completed":
                return json.loads(line.removeprefix("data: "))
            elif event == "error":
                break
    raise SmokeFailure("Live workflow stream completed without a committed turn")


def smoke(origin: str, *, workflow: bool, require_trace: bool) -> dict:
    with ExitStack() as stack:
        customer, second, staff = [
            stack.enter_context(
                httpx.Client(base_url=origin, headers={"Origin": origin}, timeout=95)
            )
            for _ in range(3)
        ]
        expect(customer.get("/").status_code == 200, "Frontend")
        expect(customer.get("/health/live").status_code == 200, "Liveness")
        ready = customer.get("/health/ready")
        expect(ready.status_code == 200 and ready.json().get("database") == "ready", "Readiness")
        expect(
            ready.headers.get("strict-transport-security") and ready.headers.get("x-request-id"),
            "Release response headers",
        )
        expect(customer.get("/openapi.json").status_code == 404, "Private API schema")
        expect(customer.get("/api/orders").status_code == 401, "Unauthenticated orders")
        sign_in(customer, "CUSTOMER", "customer")
        stack.callback(sign_out, customer)
        sign_in(second, "SECOND_CUSTOMER", "customer")
        stack.callback(sign_out, second)
        sign_in(staff, "SUPPORT", "support")
        stack.callback(sign_out, staff)
        expect(customer.post("/api/auth/refresh").status_code == 200, "Session refresh")
        expect(
            customer.post(
                "/api/auth/refresh", headers={"Origin": "https://untrusted.invalid"}
            ).status_code
            == 403,
            "Origin restriction",
        )
        orders = customer.get("/api/orders")
        expect(orders.status_code == 200 and orders.json(), "Synthetic customer orders")
        order = orders.json()[0]["id"]
        expect(second.get(f"/api/orders/{order}").status_code == 404, "Cross-customer isolation")
        expect(customer.get("/api/admin/summary").status_code == 403, "Staff access restriction")
        expect(staff.get("/api/admin/summary").status_code == 200, "Staff summary")
        report = {"https_auth_health_and_isolation": "passed"}
        if workflow:
            conversation = customer.post("/api/conversations")
            expect(conversation.status_code == 201, "Create synthetic conversation")
            conversation_id = conversation.json()["id"]
            url = f"/api/conversations/{conversation_id}/turns"
            body = {"message": "Explain the approved policy for returning gift cards."}
            headers = {"Idempotency-Key": uuid4().hex}
            data = committed_turn(customer, url, body, headers)
            expect(
                data["intent"] == "policy"
                and any(
                    c["source"] == "returns" and c["rules_version"] == "amazora-returns-v1.0"
                    for c in data["citations"]
                )
                and data["refund"] is None
                and data["escalation"] is None,
                "Cited policy response without side effects",
            )
            replay = customer.post(url, json=body, headers=headers)
            expect(
                replay.status_code == 200 and replay.json().get("replayed") is True,
                "Committed turn replay",
            )
            request = staff.get("/api/admin/executions", params={"limit": 100})
            expect(request.status_code == 200, "Staff execution timeline")
            execution = next(
                (row for row in request.json() if row["id"] == data["execution_id"]), {}
            )
            expect(
                execution.get("trace_id") and execution["status"] == "succeeded",
                "Persisted execution trace",
            )
            expect(
                not require_trace or execution["trace_delivery"] == "submitted",
                "LangSmith submission",
            )
            report.update(
                live_cited_workflow="passed",
                trace_id=execution["trace_id"],
                trace_delivery=execution["trace_delivery"],
            )
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", action="store_true", help="Run a live synthetic policy turn")
    parser.add_argument("--require-trace", action="store_true", help="Require LangSmith submission")
    args = parser.parse_args()
    try:
        origin = os.getenv("CANOPY_SMOKE_BASE_URL", "").rstrip("/")
        url = urlsplit(origin)
        expect(
            url.scheme == "https"
            and url.hostname
            and origin == f"https://{url.netloc}"
            and url.username is None
            and url.password is None,
            "CANOPY_SMOKE_BASE_URL must be an HTTPS origin",
        )
        result = smoke(
            origin, workflow=args.workflow or args.require_trace, require_trace=args.require_trace
        )
    except SmokeFailure as error:
        print(json.dumps({"status": "failed", "check": str(error)}), file=sys.stderr)
        return 1
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        print('{"status":"failed","check":"HTTPS request or release response"}', file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
