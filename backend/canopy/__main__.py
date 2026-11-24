import argparse
import json
import sys
from datetime import UTC, date, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from canopy.config import Settings
from canopy.db import make_engine
from canopy.llm import GeminiModel, ModelUnavailable
from canopy.models import Customer
from canopy.seed import CUSTOMERS, seed_data, seed_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Canopy backend administration")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "check-config", help="Show safe configuration status without making requests"
    )
    commands.add_parser("gemini-smoke", help="Make one tiny synthetic Gemini request")
    seed = commands.add_parser("seed", help="Insert synthetic Amazora records, idempotently")
    seed.add_argument("--as-of", type=date.fromisoformat, default=datetime.now(UTC).date())
    link = commands.add_parser("link-customer", help="Link a demo customer to a Supabase user UUID")
    link.add_argument("--customer", choices=[customer[0] for customer in CUSTOMERS], required=True)
    link.add_argument("--auth-user-id", type=UUID, required=True)
    args = parser.parse_args()
    try:
        settings = Settings()
    except ValidationError:
        print("Invalid configuration. Check backend/.env against .env.example.", file=sys.stderr)
        return 2
    if args.command == "check-config":
        print(
            json.dumps(
                {
                    "environment": settings.environment,
                    "database_driver": settings.database_url.get_secret_value().split(":")[0],
                    "supabase_configured": settings.auth_configured,
                    "gemini_configured": bool(settings.gemini_api_key.get_secret_value()),
                    "gemini_model": settings.gemini_model,
                    "max_output_tokens": settings.gemini_max_output_tokens,
                    "note": (
                        "Configuration presence only; this does not verify external connections"
                    ),
                },
                indent=2,
            )
        )
        return 0
    if args.command == "gemini-smoke":
        model = None
        try:
            model = GeminiModel(settings)
            result = model.generate(
                "Reply with exactly canopy-ready",
                system="This is a synthetic connectivity test. Reply briefly; use no tools.",
            )
            if result.text.lower().strip(" .\n\"'`") != "canopy-ready":
                raise ModelUnavailable("Gemini responded but the smoke-test output did not match")
            print(
                json.dumps(
                    {
                        "status": "passed",
                        "model": result.model,
                        "input_tokens": result.input_tokens,
                        "output_tokens": result.output_tokens,
                    }
                )
            )
            return 0
        except ModelUnavailable as error:
            print(str(error), file=sys.stderr)
            return 2
        finally:
            if model is not None:
                model.close()
    if settings.environment == "production":
        print("Demo seed and account-link commands are disabled in production.", file=sys.stderr)
        return 2
    engine = make_engine(settings)
    try:
        with Session(engine) as db, db.begin():
            if args.command == "seed":
                print(json.dumps(seed_data(db, args.as_of)))
            elif args.command == "link-customer":
                customer = db.get(Customer, seed_id("customer", args.customer))
                if customer is None:
                    print("Run migrations and seed before linking a customer.", file=sys.stderr)
                    return 2
                if customer.auth_user_id is not None and customer.auth_user_id != args.auth_user_id:
                    print(
                        "This customer is already linked; refusing to overwrite.", file=sys.stderr
                    )
                    return 2
                other = db.scalar(
                    select(Customer).where(
                        Customer.auth_user_id == args.auth_user_id, Customer.id != customer.id
                    )
                )
                if other:
                    print("That Auth user is already linked to another customer.", file=sys.stderr)
                    return 2
                customer.auth_user_id = args.auth_user_id
                print("Customer linked. Identity will be verified by Supabase at sign-in.")
        return 0
    except SQLAlchemyError:
        print("Database command failed. Check the connection and run migrations.", file=sys.stderr)
        return 2
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
