# Handoff for the next development session

Updated 24 November 2026. Read this file, `AGENTS.md`, and `docs/plan.md` before changing code.
Machine-specific notes are in ignored `.local/HANDOFF.md` on the maintainer's machine. Public clones
must configure their own services. Never print secret configuration to reconstruct context.

## Owner's decisions

- Build in phases and review each completed phase. Backend comes first; UI is paused.
- Use Gemini initially, with `gemini-3.5-flash-lite`; the server adapter and live connectivity are verified.
- Amazora policy rules were accepted; current deterministic version is `amazora-returns-v1.0`.
- Publish a new public GitHub repository under `ellacmd`, using proper commits and CI checks.
- License decision is deferred. Do not add an open-source license without the owner's choice.
- The current request is repository setup and handoff, not implementation of Phase 3.

## Completed product work

Phase 1: FastAPI application/configuration, Supabase Auth and HttpOnly cookies, verified role checks,
customer ownership filtering, SQLAlchemy models, Alembic migrations, reproducible synthetic Amazora
data, server-only Gemini adapter, health endpoints, and isolated tests. Hosted Supabase development
infrastructure and a local Docker fallback exist. The product frontend/backend are not deployed.

Phase 2: order-number lookup/tracking; pure deterministic item-level eligibility; explicit confirmation;
simulated refund submission/status; customer-scoped idempotency; atomic quantity/value reservations;
refund lines and audit events; customer escalation; support/admin ticket assignment/status and refund
review. Migration head is `0003`, with thirteen domain tables. Cloud and local fallback are migrated.
See `docs/phase-2-review.md` for an API walkthrough and live cloud verification results.

Refunds are always simulated and submitted. There is no payment integration, approval/completion,
rejection, or reservation-release endpoint. The model cannot authorize a refund. Whole item quantities
can be submitted separately, but total reserved quantity/value cannot exceed the purchase. Old active
requests without item allocations reserve their whole order; unknown historical consent stays unknown.

The standard window includes the entire 30th UTC calendar day after recorded delivery. Damage,
wrong-item/other exceptions, excluded items, missing delivery facts, unsupported currency, and unresolved
amount allocations require human review. Item paid amounts must reconcile to the order total.
Read `docs/amazora-rules.md` rather than inventing policy details.

## Code map

| Area | Files |
|---|---|
| API and identity | `backend/canopy/api.py`, `auth.py`, `support_api.py` |
| Business operations | `backend/canopy/tools.py`, `policy.py`, `contracts.py` |
| Data and migrations | `backend/canopy/models.py`, `db.py`, `backend/migrations/` |
| Provider and setup | `backend/canopy/llm.py`, `local_setup.py`, `seed.py`, `__main__.py` |
| Verification | `backend/tests/`, `.github/workflows/ci.yml` |
| Public-repo tooling | `.githooks/`, `scripts/`, `.github/dependabot.yml` |
| Plans and policy | `docs/plan.md`, `architecture.md`, `amazora-rules.md` |

## Repository setup

Git starts from an honest baseline of Phases 1–2. Commit hooks enforce Conventional Commit subjects,
private-file checks, staged secret scanning, lint, format, and actionlint. Actions are SHA-pinned and run
without real service credentials. CI includes Python 3.12/PostgreSQL 15 and Python 3.13/PostgreSQL 17,
dependency audits, full-history secret scanning, and backend wheel builds. Dependabot covers uv/npm/actions.
See `CONTRIBUTING.md` and `docs/repository-workflow.md` for commands and branch settings.

Before repository setup, all 95 backend tests passed with actual disposable PostgreSQL enabled.
Repository setup adds five index-guard test cases; all **100 tests passed locally**, including actual
PostgreSQL integration. Lint, format, actionlint, staged secret scan, Python/npm audits, and wheel build
also passed. Consult the latest GitHub CI run for remote results.
Real cloud workflow checks passed separately. Do not confuse mocked provider tests with live checks.

The parked Next.js scaffold has no application routes. No frontend build/E2E gate is active yet.
Deployment/CD remains Phase 6; hosts, production environment, release artifacts, and rollback need
to be selected before adding deployment jobs. No CI job migrates the live cloud database.

## Next product phase, when requested

Phase 3 is policy retrieval with citations:

1. Define a small versioned Markdown corpus consistent with the approved deterministic rules.
2. Choose an affordable embedding model separately from chat; verify current pricing, model access,
   dimensions, and terms using official documentation. Freeze dimensions/version in schema.
3. Add pgvector schema/migrations, ingestion/chunking, repeatable indexing, and backend-only search.
4. Return source/version citations and explicit missing/conflicting-evidence behavior.
5. Evaluate retrieval on repeatable queries before adding PDF ingestion or more document management.

Phase 4 adds LangGraph orchestration, Gemini intent/grounded responses, checkpoint memory, explicit
confirmation state, streaming, and sanitized traces. Establish measured cost/latency and at least 30
evaluation cases. Phase 5 builds the UI on verified contracts. Phase 6 handles release hardening/deployment.

## Important limits and working rules

Keep Supabase CRUD server-only, RLS forced and client access denied. Runtime uses `service_role`;
migrations use the owner. Use verified Auth IDs/app_metadata and ownership filters on every tool.
Do not expose auth tokens in JavaScript or let editable metadata grant roles.

The login limiter is process-local; shared rate limiting and enforced usage budgets remain release work.
Session refresh coordination belongs to the future UI. Logout revokes refresh sessions; issued access
tokens may remain valid until expiry. Future traces must contain sanitized metadata, not raw customer data.

Never use the shared/cloud database as `POSTGRES_TEST_URL`, reset it, or re-date shared seed records.
No Gemini request is needed for lint, tests, health checks, or Phase 2 APIs. Do not implement new UI or
advance phases merely because the context changed. Report actual verification and ask only for missing
decisions that materially block the requested next task.
