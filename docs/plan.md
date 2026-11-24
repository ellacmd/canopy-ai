# Canopy AI implementation plan

## Working agreement

Build in phases, review each result, and keep UI implementation paused until the backend workflows work.
Gemini is the selected initial provider. Start with `gemini-3.5-flash-lite` and validate model access with
a synthetic smoke test before using it in workflows. Model quality is not established until evaluation.

## Phase 1 — Foundation and data

Implemented locally:

- FastAPI application factory, explicit configuration, liveness/readiness endpoints.
- Backend Supabase Auth integration, HttpOnly cookies, role checks, customer ownership checks.
- Eleven domain tables, reversible migrations, backend-only PostgreSQL access restrictions.
- Reproducible synthetic customers, products, orders, items, and shipments.
- Server-only Gemini adapter and explicit connection-test command; bounded requests and safe errors.
- Isolated foundation tests and a CI workflow with a disposable PostgreSQL service.

Phase 1 review gate:

- [x] Gemini configured and live smoke test passed (`gemini-3.5-flash-lite`, 22 input tokens,
  4 output tokens). This verifies connectivity, not support-workflow quality.
- [x] Hosted **Canopy AI** Supabase project created on the Free plan in `eu-west-1`; PostgreSQL migrations
  and synthetic seed data applied. An isolated local stack remains available as a fallback.
- [x] Three synthetic customer Auth accounts plus support/admin created; real sign-in, refresh/logout,
  cross-customer denial, and role restrictions verified against both local and hosted Supabase.
- [x] PostgreSQL integration test passed in a disposable database; full suite: 42 passed.
- [x] Amazora policy rules accepted by the project owner on 24 November 2026; see `amazora-rules.md`.

No UI, agent orchestration, policy indexing, financial action endpoint, or frontend/backend hosting deployment
in this phase. The managed Supabase development infrastructure is provisioned.

Local verification on 24 November 2026: all 42 tests passed, including real PostgreSQL migrations,
role switching, forced RLS, and client database denial. Alembic reported no schema drift.
The API returned 200 for liveness, readiness, and documentation, and 401 for unauthenticated orders.
The separate local setup verification passed real Supabase sign-in, customer isolation, role checks,
refresh, logout, and direct PostgREST denial. Gemini's live smoke test passed after the key was added.

The maintainer's active backend configuration uses a hosted Supabase development project on the
Free plan in `eu-west-1`, using the session pooler on port 5432 over TLS. Hosted sign-in, refresh/logout,
customer isolation, support/admin restrictions, direct client database denial, and forced RLS passed.
Synthetic account credentials remain in ignored `backend/.env.demo` with restricted permissions.
Local fallback settings remain in ignored `backend/.env.supabase-local`; cloud identity is in ignored
`supabase/cloud.json`. Public clones must configure their own environment.

## Phase 2 — Business tools and deterministic rules

Implemented order-number lookup, tracking, item-level eligibility, explicit confirmation, simulated
refund submission/status, customer escalations, and support/admin ticket management and refund listing.
Migration `0003` adds item/value reservations, refund lines, ownership constraints, and atomic audit records;
there are now thirteen domain tables. PostgreSQL row locks and conditional updates prevent duplicate
quantities even when requests use different keys. Customer-scoped idempotency supports safe retries.

The approved return policy includes day 30 through its full UTC calendar day, exact paid amounts,
partial quantities, and human review for damage, excluded items, and inconsistent data. Requests are
simulated and submitted; no payment or automatic completion occurs. See `phase-2-review.md`.

Gate: API/function tests cover ownership, exact amounts, boundaries, rollback, concurrent submissions,
idempotency, legacy migration, and human-review exceptions. No LLM authorizes monetary actions.
Phase 2 is the current review point; Phase 3 and UI work remain deferred.

Verification on 24 November 2026: all 95 tests passed with disposable PostgreSQL integration enabled.
Cloud and local migrations reached `0003`; cloud schema drift check passed. Real cloud HTTP checks
verified simulated refund submission/replay, duplicate rejection, customer isolation, human escalation,
support assignment, role restrictions, refresh, and logout. No financial payment was made.

## Phase 3 — Policy retrieval

Start with versioned Markdown policies, chunking, embeddings, pgvector search, and citations.
Select and validate the embedding model separately from the chat model; freeze its dimensions in schema.
Add PDF/text ingestion, private storage, processing states, document administration, and retry behavior
after retrieval quality is established.

Gate: queries retrieve the right source/version; missing or conflicting sources never produce invented policy.

## Phase 4 — LangGraph support workflow

Use one orchestrating workflow with specialized nodes/tools. Add Gemini intent classification and grounded
response generation, PostgreSQL checkpoint memory, explicit confirmation states, streaming, validated
outputs, internal execution records, and redacted LangSmith traces.
Retrieve order and policy context concurrently only where independent.
Do not add autonomous worker agents without measured benefit.

Gate: complete order, policy, refund, and escalation flows through the authenticated API across multiple turns.
Establish measured cost/latency baselines on at least 30 evaluation cases.

## Phase 5 — Web application

Build customer chat/history, support inbox, customer/order context, refund review, policy administration,
operations dashboard, and execution timelines on the verified backend contracts.
Handle session refresh once per browser session at a time to avoid token-rotation races.
Include accessible keyboard interactions, responsive layouts, and clear success/error/loading states.

Gate: both customer and support roles complete their workflows in a real browser against the backend.

## Phase 6 — Reliability and release

Expand evaluations and failure testing, add shared rate limits and enforced usage budgets, verify secure
production configuration, health checks, CI, migrations, redaction, and browser behavior.
Deploy frontend/backend/Supabase, document measured accuracy/latency/cost, and prepare portfolio evidence.

Gate: core tests pass in the deployment configuration, live workflows and traceability work, and results
are reproducible. Do not label targets as measured performance.
