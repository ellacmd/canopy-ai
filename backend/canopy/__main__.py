import argparse
import json
import sys
from datetime import UTC, date, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from canopy.admission import Admission
from canopy.config import Settings
from canopy.corpus import CorpusInvalid
from canopy.db import make_engine
from canopy.embedding import EMBEDDING_SPACE, EmbeddingUnavailable, GeminiEmbedder
from canopy.llm import GeminiModel, ModelUnavailable
from canopy.models import Customer
from canopy.retrieval import IndexConflict, SearchRequest, index_corpus, search_policy
from canopy.retrieval_eval import evaluate_retrieval
from canopy.seed import CUSTOMERS, seed_data, seed_id


def policy_command(settings: Settings, args) -> int:
    adapter = engine = None
    try:
        adapter = GeminiEmbedder(settings)
        if args.command == "embedding-smoke":
            document = adapter.embed(["Amazora standard returns allow 30 days."], task="document")
            query = adapter.embed(["What is the standard return window?"], task="query")
            print(
                json.dumps(
                    {
                        "status": "passed",
                        "embedding_space": EMBEDDING_SPACE,
                        "document_dimensions": len(document[0]),
                        "query_dimensions": len(query[0]),
                    }
                )
            )
            return 0
        if args.command == "policy-search" and not settings.policy_search_enabled:
            print(
                "Enable POLICY_SEARCH_ENABLED after reviewing provider data terms.", file=sys.stderr
            )
            return 2
        engine = make_engine(settings)
        with Session(engine) as db, db.begin():
            if args.command == "policy-index":
                result = index_corpus(db, adapter)
            elif args.command == "policy-evaluate":
                result = evaluate_retrieval(db, adapter)
            else:
                result = search_policy(db, adapter, SearchRequest(query=args.query)).model_dump()
        print(json.dumps(result, indent=2))
        return int(args.command == "policy-evaluate" and result["passed"] != result["cases"])
    except (CorpusInvalid, EmbeddingUnavailable, IndexConflict) as error:
        print(str(error), file=sys.stderr)
        return 2
    except ValidationError:
        print("Invalid policy search input.", file=sys.stderr)
        return 2
    except SQLAlchemyError:
        print(
            "Policy database command failed. Check the connection and migrations.", file=sys.stderr
        )
        return 2
    finally:
        if adapter is not None:
            adapter.close()
        if engine is not None:
            engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Canopy backend administration")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "check-config", help="Show safe configuration status without making requests"
    )
    commands.add_parser("gemini-smoke", help="Make one tiny synthetic Gemini request")
    commands.add_parser("prune-admission", help="Remove expired request and budget counters")
    workflow_eval = commands.add_parser(
        "workflow-evaluate", help="Live synthetic workflow evaluation in a dedicated local database"
    )
    workflow_eval.add_argument("--interval-seconds", type=float, default=4)
    workflow_eval.add_argument("--max-attempts", type=int, choices=range(1, 5), default=1)
    workflow_eval.add_argument("--suite", choices=("baseline", "release"), default="baseline")
    commands.add_parser("embedding-smoke", help="Verify document/query access with synthetic text")
    commands.add_parser("policy-index", help="Atomically index the approved public Markdown corpus")
    commands.add_parser(
        "policy-evaluate", help="Run fixed synthetic retrieval cases (provider calls)"
    )
    search = commands.add_parser("policy-search", help="Retrieve cited policy evidence")
    search.add_argument("--query", required=True)
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
                    "embedding_space": EMBEDDING_SPACE,
                    "policy_search_enabled": settings.policy_search_enabled,
                    "workflow_enabled": settings.workflow_enabled,
                    "workflow_tracing_enabled": settings.workflow_tracing_enabled,
                    "admission_key_configured": bool(settings.admission_key.get_secret_value()),
                    "gemini_chat_calls_per_day": settings.gemini_chat_calls_per_day,
                    "gemini_embedding_calls_per_day": settings.gemini_embedding_calls_per_day,
                    "note": (
                        "Configuration presence only; this does not verify external connections"
                    ),
                },
                indent=2,
            )
        )
        return 0
    if args.command == "prune-admission":
        engine = make_engine(settings)
        try:
            print(json.dumps({"expired_counters_removed": Admission(engine, settings).prune()}))
            return 0
        except SQLAlchemyError:
            print("Counter cleanup failed. Check the connection and migrations.", file=sys.stderr)
            return 2
        finally:
            engine.dispose()
    if args.command == "workflow-evaluate":
        from canopy.workflow_eval import evaluate_workflow

        try:
            result = evaluate_workflow(
                settings,
                interval=args.interval_seconds,
                max_attempts=args.max_attempts,
                suite=args.suite,
            )
            print(json.dumps(result, indent=2))
            return int(result["passed"] != result["cases"])
        except Exception:
            print(
                "Workflow evaluation failed. Check the dedicated local evaluation "
                "database and provider configuration.",
                file=sys.stderr,
            )
            return 2
    if args.command in {"embedding-smoke", "policy-index", "policy-search", "policy-evaluate"}:
        return policy_command(settings, args)
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
