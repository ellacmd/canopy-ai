import httpx
from browser_server import auth_provider


def test_demo_session_works_after_recreating_the_auth_provider(tmp_path):
    path = tmp_path / "sessions.db"
    base = "https://auth.amazora.example/auth/v1"
    with httpx.Client(transport=httpx.MockTransport(auth_provider(path))) as first:
        response = first.post(
            f"{base}/token?grant_type=password",
            json={"email": "admin@amazora.example", "password": "synthetic-password"},
        )
        assert response.status_code == 200
        tokens = response.json()
    with httpx.Client(transport=httpx.MockTransport(auth_provider(path))) as restarted:
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        assert (
            restarted.get(f"{base}/user", headers=headers).json()["app_metadata"]["role"] == "admin"
        )
        assert restarted.post(f"{base}/logout", headers=headers).status_code == 204
        assert restarted.get(f"{base}/user", headers=headers).status_code == 401
        assert (
            restarted.post(
                f"{base}/token?grant_type=refresh_token",
                json={"refresh_token": tokens["refresh_token"]},
            ).status_code
            == 401
        )
