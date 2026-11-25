# Handoff for the next development session

Updated 25 November 2026. Read this file, `AGENTS.md`, and `docs/plan.md` before changing code.
Machine-specific notes are in ignored `.local/HANDOFF.md` on the maintainer's machine. Public clones
must configure their own services. Never print secret configuration to reconstruct context.


## Completed product work

Phase 1: FastAPI application/configuration, Supabase Auth and HttpOnly cookies, verified role checks,
customer ownership filtering, SQLAlchemy models, Alembic migrations, reproducible synthetic Amazora
data, server-only Gemini adapter, health endpoints, and isolated tests. Hosted Supabase development
infrastructure and a local Docker fallback exist. The product frontend/backend are not deployed.

Phase 2: order-number lookup/tracking; pure deterministic item-level eligibility; explicit confirmation;
simulated refund submission/status; customer-scoped idempotency; atomic quantity/value reservations;
refund lines and audit events; customer escalation; support/admin ticket assignment/status and refund
review. Phase 2 reached migration `0003`, with thirteen domain tables.
See `docs/phase-2-review.md` for an API walkthrough and live cloud verification results.

Phase 3: three approved versioned Markdown sources, eleven intact chunks, a hash-pinned packaged
manifest, independent Gemini embeddings, atomic/repeatable indexing, pgvector cosine search with
keyword reranking, and authenticated policy evidence with source/version/hash citations. Migration
head was `0004`, with fourteen domain tables; hosted development and the local fallback were migrated
and indexed.
See `docs/phase-3-review.md` for setup, model references, evaluation, boundaries, and recovery.

Embedding space is frozen as `gemini-embedding-001:768:unit-v1`; source rules remain
`amazora-returns-v1.0`. Missing or conflicting evidence yields no citations and explicitly requires
human review. Retrieved excerpts do not decide eligibility or authorize refunds. Legacy/historical
documents are excluded from current evidence. Source/version edits are immutable; explicit rerun
can repair missing chunks from the approved corpus. No arbitrary uploads or document admin were added.

Customer query embedding defaults to disabled (`POLICY_SEARCH_ENABLED=false`) until appropriate
provider data terms are configured. No billing plan was changed. Public/synthetic `embedding-smoke`,
`policy-index`, and `policy-evaluate` commands work without that flag. Real Auth/API verification used
synthetic queries with a temporary in-process opt-in; active settings remain unchanged.

Phase 4: one LangGraph coordinates Gemini structured intent classification, independent order/policy
retrieval, deterministic actions, and constrained fact/citation composition. Owned JSON checkpoint
memory, conversation history, explicit expiring refund quotes, turn replay, progress/validated SSE,
safe execution usage/cost metadata, and optional metadata-only LangSmith export are implemented.
Current migration head is `0005`, with seventeen domain tables; hosted development and local fallback
are migrated. The local backend runs Phase 4 routes. See `docs/phase-4-review.md` for contracts and limits.

Messages cannot authorize refunds. A separate explicit action must confirm a specific active owned quote,
current policy version, exact current amount, and quantities within 15 minutes. New instructions can
invalidate a preview; same-key turn retries replay committed results. The checkpoint, quote consumption,
messages, execution, replay result, refund/reservations, and audit commit atomically. Persistence is at
turn boundaries, not mid-node resume/time travel. Changed provider settings/models require evaluation.

`WORKFLOW_ENABLED` defaults to false until appropriate provider data terms are configured. No billing
plan changed. Automatic graph tracing is always suppressed. `WORKFLOW_TRACING_ENABLED` plus a
backend-only LangSmith key enables manual allowlisted metadata export; no key was configured, so
external delivery has not been live-verified. Internal trace IDs do not prove external delivery.

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
| Policy evidence | `backend/canopy/corpus/`, `corpus.py`, `embedding.py`, `retrieval.py`, `policy_api.py`, `retrieval_eval.py` |
| Support graph | `backend/canopy/workflow.py`, `workflow_api.py`, `workflow_contracts.py`, `checkpoints.py`, `workflow_traces.py`, `workflow_eval.py`, `evaluations/` |
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

Phase 3 verification: **131 tests passed** with actual disposable PostgreSQL/pgvector, including
concurrent indexing, vectors, RLS, migration reversal, schema drift, and Supabase extension-schema
compatibility. The live hosted evaluation passed **34/34** (26 expected source/version retrievals and
8 unsupported-query abstentions). The suite was used during tuning; it is not an independent holdout.
Saved details: `docs/evaluations/phase-3-retrieval.json`. Hosted schema drift, synthetic customer/support
Auth plus policy API, local process health/readiness, and repeatable indexing were checked separately.
See the Phase 3 review for final quality/security/build checks. No Phase 3 push or remote CI run occurred.

Phase 4 verification: **157 tests passed** with actual disposable PostgreSQL/pgvector. Hosted schema
drift, seventeen forced-RLS tables, new-table client denial, local process readiness, real Auth/SSE,
preview/cancel/confirmed simulated submission/replay, customer isolation, and support ticket management
passed. A new synthetic cloud review order was added; original shared seed records/dates were preserved.
The final live evaluation attempted **36 cases: 25 passed, 11 unavailable** after bounded explicit
evaluation retries, because of provider/network errors/timeouts. This is not a passing reliability gate.
The unfiltered report is `docs/evaluations/phase-4-workflow.json`; see `docs/phase-4-review.md` for measured
usage/latency, incomplete cost estimates, and limitations. LangSmith export has unit coverage but no
live delivery verification. No Phase 4 push, product deployment, or remote CI run occurred.

The parked Next.js scaffold has no application routes. No frontend build/E2E gate is active yet.
Deployment/CD remains Phase 6; hosts, production environment, release artifacts, and rollback need
to be selected before adding deployment jobs. No CI job migrates the live cloud database.

## Next product phase, when requested

Phase 4 is the current review point. Review workflow/provider measurements in `docs/phase-4-review.md`
and retrieval limits in `docs/phase-3-review.md` before expanding the corpus or enabling real customer
messages. Ordinary tests mock providers; separate live evaluations report actual behavior and failures.
The fixed fixtures are regression cases, not independent holdouts or broad customer accuracy claims.

Phase 5 builds the UI on verified contracts, including explicit quote review/confirmation, same-key
retries, SSE error handling, scoped history, and coordinated Auth refresh. Phase 6 handles release
hardening/deployment, shared limits/budgets, and operational retention/recovery. Do not advance phases
or add uploads/document administration without the owner's request.

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
