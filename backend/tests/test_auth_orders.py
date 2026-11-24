from uuid import uuid4

import pytest

from canopy.seed import seed_id


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_unauthenticated_and_invalid_tokens_are_rejected(client):
    assert client.get("/api/orders").status_code == 401
    assert client.get("/api/orders", headers=auth("invalid")).status_code == 401


def test_customer_only_sees_owned_orders(client):
    maya = client.get("/api/orders", headers=auth("maya-token"))
    jordan = client.get("/api/orders", headers=auth("jordan-token"))
    assert maya.status_code == jordan.status_code == 200
    assert len(maya.json()) == 6
    assert len(jordan.json()) == 3
    assert set(row["id"] for row in maya.json()).isdisjoint(row["id"] for row in jordan.json())


def test_detail_contains_exact_money_items_and_tracking(client):
    response = client.get(f"/api/orders/{seed_id('order', 'AMZ-1024')}", headers=auth("maya-token"))
    assert response.status_code == 200
    order = response.json()
    assert order["total_amount"] == "89.00"
    assert order["items"][0]["sku"] == "HEAD-01"
    assert order["shipment"]["tracking_number"] == "SYNTHETIC-AMZ-1024"


def test_foreign_and_nonexistent_orders_return_identical_not_found(client):
    foreign = client.get(f"/api/orders/{seed_id('order', 'AMZ-1027')}", headers=auth("maya-token"))
    missing = client.get(f"/api/orders/{uuid4()}", headers=auth("maya-token"))
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


@pytest.mark.parametrize("token", ["maya-token", "forged-role", "unknown-role"])
def test_customer_cannot_gain_support_access_from_user_metadata(client, token):
    assert client.get("/api/admin/summary", headers=auth(token)).status_code == 403


@pytest.mark.parametrize("token", ["support-token", "admin-token"])
def test_support_and_admin_can_read_counts_but_not_impersonate_customers(client, token):
    response = client.get("/api/admin/summary", headers=auth(token))
    assert response.status_code == 200
    assert response.json() == {"customers": 3, "orders": 11}
    assert client.get("/api/orders", headers=auth(token)).status_code == 403


def test_unlinked_account_is_denied(client):
    assert client.get("/api/orders", headers=auth("unlinked-token")).status_code == 403


def test_login_refresh_and_logout_use_http_only_cookies(client):
    response = client.post(
        "/api/auth/login",
        json={
            "email": "maya@amazora.example",
            "password": "synthetic-password",
        },
    )
    assert response.status_code == 200
    assert response.json()["role"] == "customer"
    assert "access_token" not in response.text and "refresh" not in response.text
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 2
    assert all("HttpOnly" in cookie and "SameSite=lax" in cookie for cookie in cookies)
    assert client.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/refresh").status_code == 200
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_bad_credentials_are_generic_and_not_reflected(client):
    response = client.post(
        "/api/auth/login",
        json={
            "email": "maya@amazora.example",
            "password": "private-password",
        },
    )
    assert response.status_code == 401
    assert "private-password" not in response.text
    assert "Provider-specific" not in response.text


def test_validation_does_not_echo_secrets(client):
    secret = "sensitive-password-" * 30
    response = client.post("/api/auth/login", json={"email": "bad", "password": secret})
    assert response.status_code == 422
    assert "sensitive-password" not in response.text


def test_cookie_mutations_reject_untrusted_origins(client):
    response = client.post(
        "/api/auth/login",
        headers={"Origin": "https://evil.example"},
        json={
            "email": "maya@amazora.example",
            "password": "synthetic-password",
        },
    )
    assert response.status_code == 403
    assert "canopy_access" not in client.cookies


def test_api_documentation_origin_can_sign_in(client):
    response = client.post(
        "/api/auth/login",
        headers={"Origin": "http://127.0.0.1:8000", "Sec-Fetch-Site": "same-origin"},
        json={"email": "maya@amazora.example", "password": "synthetic-password"},
    )
    assert response.status_code == 200
    assert client.get("/api/orders").status_code == 200


def test_login_attempts_are_bounded(client):
    for _ in range(10):
        response = client.post(
            "/api/auth/login",
            json={
                "email": "maya@amazora.example",
                "password": "wrong",
            },
        )
        assert response.status_code == 401
    assert (
        client.post(
            "/api/auth/login",
            json={
                "email": "maya@amazora.example",
                "password": "wrong",
            },
        ).status_code
        == 429
    )


def test_pagination_and_input_validation(client):
    headers = auth("maya-token")
    all_orders = client.get("/api/orders", headers=headers).json()
    assert client.get("/api/orders?limit=2&offset=2", headers=headers).json() == all_orders[2:4]
    assert client.get("/api/orders?limit=1000", headers=headers).status_code == 422
    assert client.get("/api/orders/not-a-uuid", headers=headers).status_code == 422


def test_health_and_no_store_headers(client):
    assert client.get("/health/live").status_code == 200
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert len(response.headers["x-request-id"]) == 32
