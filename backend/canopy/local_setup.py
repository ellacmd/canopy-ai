"""Configure this project's local Supabase and verify it without printing credentials.

Run from backend/: uv run python -m canopy.local_setup --start
"""

import argparse
import json
import os
import re
import secrets
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
from dotenv import dotenv_values, set_key
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from canopy.api import create_app
from canopy.config import Settings
from canopy.db import Base, make_engine
from canopy.models import Customer
from canopy.seed import CUSTOMERS, seed_data, seed_id

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent


def update_env(path: Path, updates: dict[str, str]) -> None:
    """Preserve existing values (especially Gemini) and replace the file atomically."""
    descriptor, name = tempfile.mkstemp(prefix=".env-setup-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w") as target:
            target.write(path.read_text() if path.exists() else "")
        for key, value in updates.items():
            set_key(temporary, key, value, quote_mode="always")
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def command(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if result.returncode:
        # The CLI's success output includes keys; never relay raw output or provider exceptions.
        raise RuntimeError(f"{args[0]} {args[1]} failed (exit {result.returncode})")
    return result


def start_services() -> None:
    network = "canopy-ai-local"
    option = "com.docker.network.bridge.host_binding_ipv4"
    inspected = subprocess.run(
        ["docker", "network", "inspect", network], capture_output=True, text=True
    )
    if inspected.returncode:
        command(
            [
                "docker",
                "network",
                "create",
                "--driver",
                "bridge",
                "--opt",
                f"{option}=127.0.0.1",
                network,
            ],
            ROOT,
        )
    elif json.loads(inspected.stdout)[0].get("Options", {}).get(option) != "127.0.0.1":
        raise RuntimeError("The Canopy Docker network must bind published ports to localhost")
    excluded = (
        "imgproxy,edge-runtime,logflare,vector,realtime,supavisor,"
        "storage-api,studio,postgres-meta,mailpit"
    )
    process = subprocess.Popen(
        ["supabase", "start", "--network-id", network, "--exclude", excluded],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    layers = 0
    try:
        for line in process.stdout:
            # Print only fixed progress messages, never raw CLI output (which includes keys).
            if "Pull complete" in line:
                layers += 1
                print(f"Container image layers downloaded: {layers}", flush=True)
            elif "Starting database" in line:
                print("Starting the local PostgreSQL database.", flush=True)
            elif "Starting containers" in line:
                print("Starting the local authentication and API services.", flush=True)
            elif "Waiting for health checks" in line:
                print("Waiting for local service health checks.", flush=True)
        if process.wait():
            raise RuntimeError(
                "Supabase startup failed. Check Docker connectivity and image downloads."
            )
    except BaseException:
        process.terminate()
        process.wait()
        raise


def verify_backend(settings: Settings, accounts: dict[str, str]) -> dict:
    app = create_app(settings)
    with TestClient(app) as maya, TestClient(app) as jordan, TestClient(app) as admin:
        for client, key in [(maya, "MAYA"), (jordan, "JORDAN"), (admin, "ADMIN")]:
            response = client.post(
                "/api/auth/login",
                json={
                    "email": accounts[f"DEMO_{key}_EMAIL"],
                    "password": accounts[f"DEMO_{key}_PASSWORD"],
                },
            )
            if response.status_code != 200:
                raise RuntimeError(f"Real local sign-in failed for {key} ({response.status_code})")
        orders = maya.get("/api/orders")
        if orders.status_code != 200 or len(orders.json()) != 6:
            raise RuntimeError("Customer order retrieval failed")
        order_id = orders.json()[0]["id"]
        if jordan.get(f"/api/orders/{order_id}").status_code != 404:
            raise RuntimeError("Cross-customer isolation check failed")
        if maya.get("/api/admin/summary").status_code != 403:
            raise RuntimeError("Customer role restriction check failed")
        if admin.get("/api/admin/summary").status_code != 200:
            raise RuntimeError("Administrator access check failed")
        if admin.get("/api/orders").status_code != 403:
            raise RuntimeError("Administrator impersonation restriction check failed")
        if maya.post("/api/auth/refresh").status_code != 200:
            raise RuntimeError("Real session refresh failed")
        access_token = maya.cookies.get("canopy_access")
        with httpx.Client(timeout=10) as client:
            for token in [None, access_token]:
                headers = {"apikey": settings.supabase_publishable_key.get_secret_value()}
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                response = client.get(
                    f"{settings.supabase_url}/rest/v1/orders?select=id",
                    headers=headers,
                )
                if response.status_code not in {401, 403}:
                    raise RuntimeError("Direct client database access was not denied")
        if maya.post("/api/auth/logout").status_code != 204:
            raise RuntimeError("Real session logout failed")
        if maya.get("/api/orders").status_code != 401:
            raise RuntimeError("Signed-out request was not denied")
    engine = make_engine(settings)
    try:
        with engine.connect() as connection:
            if connection.scalar(text("SELECT current_user")) != "service_role":
                raise RuntimeError("Backend runtime is not using service_role")
            flags = dict(
                (row[0], (row[1], row[2]))
                for row in connection.execute(
                    text(
                        "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                        "WHERE relnamespace='public'::regnamespace AND relkind='r'"
                    )
                )
            )
            if not all(flags.get(table) == (True, True) for table in Base.metadata.tables):
                raise RuntimeError("A Canopy table is missing forced row-level security")
    finally:
        engine.dispose()
    return {
        "live_auth": "passed",
        "customer_isolation": "passed",
        "role_checks": "passed",
        "refresh_and_logout": "passed",
        "direct_client_database_access": "denied",
        "postgresql_rls": "passed",
    }


def setup(*, start: bool) -> dict:
    if start:
        print("Starting local Supabase. First run may download Docker images.", flush=True)
        start_services()
    status = json.loads(command(["supabase", "status", "--output", "json"], ROOT).stdout)
    api_url = status["API_URL"]
    if api_url != "http://127.0.0.1:54331":
        raise RuntimeError("Expected this project's local Supabase API on port 54331")
    db_url = make_url(status["DB_URL"])
    if db_url.host not in {"127.0.0.1", "localhost"} or db_url.port != 54332:
        raise RuntimeError("Refusing to provision a non-local or unexpected database")
    db_url = db_url.set(drivername="postgresql+psycopg")
    update_env(
        BACKEND / ".env",
        {
            "DATABASE_URL": db_url.render_as_string(hide_password=False),
            "SUPABASE_URL": api_url,
            "SUPABASE_PUBLISHABLE_KEY": status.get("PUBLISHABLE_KEY") or status["ANON_KEY"],
        },
    )
    command(["uv", "run", "--frozen", "alembic", "upgrade", "head"], BACKEND)
    settings = Settings()
    engine = make_engine(settings)
    try:
        with Session(engine) as db, db.begin():
            counts = seed_data(db, datetime.now(UTC).date())
        account_path = BACKEND / ".env.demo"
        accounts = {key: value for key, value in dotenv_values(account_path).items() if value}
        people = [(key.upper(), email, "customer") for key, _, email in CUSTOMERS]
        people.extend(
            [
                ("SUPPORT", "support@amazora.example", "support"),
                ("ADMIN", "admin@amazora.example", "admin"),
            ]
        )
        headers = {
            "apikey": status["SERVICE_ROLE_KEY"],
            "Authorization": f"Bearer {status['SERVICE_ROLE_KEY']}",
        }
        with httpx.Client(base_url=f"{api_url}/auth/v1", headers=headers, timeout=15) as client:
            listing = client.get("/admin/users", params={"page": 1, "per_page": 100})
            if not listing.is_success:
                raise RuntimeError("Cannot list local demo Auth accounts")
            existing = {user["email"]: user for user in listing.json()["users"]}
            for key, email, role in people:
                password_key = f"DEMO_{key}_PASSWORD"
                known_password = accounts.get(password_key)
                password = known_password or f"{secrets.token_urlsafe(24)}Aa1!"
                payload = {
                    "email": email,
                    "password": password,
                    "email_confirm": True,
                    "app_metadata": {"role": role},
                }
                if email not in existing:
                    response = client.post("/admin/users", json=payload)
                else:
                    # Only synthetic accounts in this dedicated local project are managed here.
                    response = client.put(f"/admin/users/{existing[email]['id']}", json=payload)
                if not response.is_success:
                    raise RuntimeError(
                        f"Creating local {key} account failed ({response.status_code})"
                    )
                user = response.json()
                accounts.update({f"DEMO_{key}_EMAIL": email, password_key: password})
                update_env(account_path, accounts)
                if role == "customer":
                    with Session(engine) as db, db.begin():
                        customer = db.get(Customer, seed_id("customer", key.lower()))
                        if customer.auth_user_id and customer.auth_user_id != UUID(user["id"]):
                            raise RuntimeError("Existing customer link differs; refusing overwrite")
                        customer.auth_user_id = UUID(user["id"])
    finally:
        engine.dispose()
    result = verify_backend(settings, accounts)
    return {
        **counts,
        **result,
        "supabase_api": api_url,
        "studio": status.get("STUDIO_URL"),
        "demo_accounts_file": "backend/.env.demo",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", action="store_true", help="Start Docker services before setup")
    args = parser.parse_args()
    try:
        print(json.dumps(setup(start=args.start), indent=2))
        return 0
    except KeyboardInterrupt:
        print("Local setup interrupted; existing data has been preserved.")
        return 130
    except Exception as error:
        # Raw provider exceptions can contain passwords/keys. Only relay our fixed local messages.
        if type(error) is RuntimeError and re.fullmatch(r"[A-Za-z0-9 ,.:;()_/-]+", str(error)):
            print(f"Setup stopped: {error}")
        else:
            print(
                "Setup stopped. Check that Docker and this project's local Supabase "
                "services are running."
            )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
