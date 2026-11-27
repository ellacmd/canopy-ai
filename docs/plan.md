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
Phase 2 passed its review gate. UI work remains deferred.

Verification on 24 November 2026: all 95 tests passed with disposable PostgreSQL integration enabled.
Cloud and local migrations reached `0003`; cloud schema drift check passed. Real cloud HTTP checks
verified simulated refund submission/replay, duplicate rejection, customer isolation, human escalation,
support assignment, role restrictions, refresh, and logout. No financial payment was made.

## Phase 3 — Policy retrieval

Implemented three hash-pinned Markdown policies with eleven bounded sections, atomic/repeatable
indexing, pgvector exact cosine candidates with keyword reranking, and authenticated cited retrieval.
The independently selected and live-tested embedding model is `gemini-embedding-001`; migration
`0004` freezes 768-dimensional vectors and their embedding space. All fourteen domain tables remain
backend-only with forced RLS and no client grants/policies. Chat is not used for retrieval or decisions.

Responses explicitly distinguish evidence found, missing evidence, and conflicting evidence. Current
source/version/hash mismatches fail closed; historical/legacy sources are excluded. Customer query
embedding is opt-in after provider data terms are configured appropriately. Explicit public/synthetic
CLI commands handle indexing, connectivity testing, search, and repeatable evaluation.

Gate: queries retrieve the right source/version; missing or conflicting sources never produce invented
policy. See `phase-3-review.md` for the implementation, official model/pricing/terms references,
measured retrieval results, test evidence, and migration recovery. Phase 3 passed its retrieval gate.
Add PDF/text uploads, private storage, processing workflows, and document administration only after
reviewing retrieval quality; they are not implemented here. UI remains deferred.

## Phase 4 — LangGraph support workflow

Implemented one LangGraph with classification, independent context retrieval, explicit actions, and
validated fact/citation composition. PostgreSQL stores owned JSON checkpoint memory, customer/assistant
history, safe execution metadata, expiring refund quotes, and turn replay records atomically. Model/text
consent cannot submit refunds. Explicit confirmed quote actions recheck policy and exact current amounts.
SSE streams progress and committed, validated responses. Optional LangSmith export sends allowlisted
metadata only; automatic graph tracing is suppressed. There are no autonomous worker agents.

Migration `0005` adds three tables and execution metadata, bringing the domain count to seventeen.
Hosted development and local fallback are migrated. Customer workflow and trace export stay opt-in
until suitable provider terms/credentials are configured; no billing plan or product deployment changed.

Gate: complete order, policy, refund, and escalation flows through the authenticated API across multiple turns.
Establish measured cost/latency baselines on at least 30 evaluation cases. The repeatable fixture contains
36 cases; tests and separate live provider measurements are documented in `phase-4-review.md`.
Phase 4 implementation is complete. See the live verification status below.
The final live run passed 25/36 cases; eleven were unavailable because of provider/network failures.
The live reliability gate remains open. LangSmith delivery is unverified without a configured key.

## Phase 5 — Web application

Implemented customer chat, cited history, order context, explicit simulated refund review,
and escalation. The staff workspace includes ticket assignment and resolution, customer/order
context, refund review, policy corpus and index inspection, and operations with execution timelines.
Browser tabs coordinate rotating session refresh with Web Locks.

Gate: Playwright covers both roles in Chromium against the real FastAPI routes and a disposable
SQLite database, with Auth and model providers simulated. The suite joins the required CI checks.
Phase 4's live provider reliability gate remains open.

## Phase 6 — Reliability and release

Migration `0006` adds forced-RLS PostgreSQL admission counters for authentication, support turns,
and Gemini calls. Production requires a certificate-verified database, explicit HTTPS origins and
shared admission credentials. Delivery includes a Compose stack, a required CI image check, an HTTPS
staging smoke test, a backup and rollback runbook, and thirteen additional synthetic evaluation cases.

The [release runbook](release.md) describes deployment and recovery. The [Phase 6 review](phase-6-review.md)
records local evidence and the status of the hosted release gate. Hosted deployment, live evaluations,
and LangSmith delivery require an approved environment and provider configuration.

## Current release follow-up — 27 November 2026

Source head is `0008`: voice/text conversation separation (`0007`) and published return-policy
history (`0008`) are implemented alongside live browser calls and admin policy controls.
Runtime retries now cover known transient chat/query-embedding errors, count every attempt against
shared quota, and keep text-provider work within a shared budget. SDK retries remain disabled.

The original live result (25/36, 11 unavailable) remains the latest captured provider evidence.
The release gate requires all 36 baseline and 13 release cases to pass using one turn attempt,
matching current fixtures and runtime settings, within an explicitly chosen p95 latency target.
See `scripts/check_release_evaluations.py` and [release](release.md). Hosting, current live evaluation,
staging HTTPS/browser/voice verification and recovery rehearsal remain required before release.
