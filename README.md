# Canopy AI

An intelligent customer support platform for the fictional e-commerce store Amazora.

**Current stage: Phase 6 release hardening, with voice calls and versioned return-policy administration.**
Customers can track orders, ask for cited policy information, review and explicitly confirm a
simulated refund, and contact support. Staff can manage tickets and inspect refund, policy index,
and execution details. All business operations use the authenticated FastAPI backend.

The source requires migration **`0008`**. Hosting and fresh live-provider release evidence remain
outstanding. See the [release requirements and runbook](docs/release.md) and
[current verification status](docs/phase-6-review.md) before deploying.

## Start the web application

Start the configured backend below, then run these commands from the repository root with Node.js 24:

```sh
npm ci
npm run dev
```

Open [Canopy AI](http://127.0.0.1:3000) and sign in with a configured Supabase customer or support
account. Next.js forwards same-origin `/api` requests to FastAPI on `http://127.0.0.1:8000`.
Set server-side `CANOPY_BACKEND_URL` before starting development or building to use a different
backend origin, and include the web origin in the backend's `ALLOWED_ORIGINS`.
Automatic refresh across tabs uses Web Locks on HTTPS and trustworthy local origins.

Chat turns and policy search respect the existing backend provider opt-ins. The browser suite uses
synthetic provider responses and a disposable database. See [browser verification](CONTRIBUTING.md#required-checks)
for the full local test command.

### Live browser calls

After configuring microphone audio processing for your Gemini project, set `VOICE_ENABLED=true`
in the backend environment and restart FastAPI. Use HTTPS or localhost. Customers can select
**Start call** from the Voice call workspace, interrupt spoken replies, mute, and end the call.
The browser runs a local speech detector before opening a caller turn. A possible interruption
briefly lowers the reply volume. The reply continues if the evidence fades, or stops when speech
is sustained. The browser keeps a short audio buffer to preserve the first words and allows
longer pauses during an unfinished thought when a live transcript is available.
Call activity shows order lookups, policy checks, refund preparation, and staff routing as they run.
Canopy can fill a refund preview from the details given on the call and sends returns that need
a person to the support inbox. Eligible refund requests require on-screen confirmation before
submission. The call stays connected across workspace pages and ends after Canopy says goodbye.
The authenticated same-origin WebSocket uses Gemini Live.
Canopy saves call transcripts separately from text chat and stores verified policy evidence.
Canopy does not store call audio. Enable `POLICY_SEARCH_ENABLED` for cited policy lookups and
`WORKFLOW_ENABLED` for text support. The browser suite uses a synthetic voice provider.

Administrators can publish return rules and section guidance under **Return policy**. The new
version applies to new previews, and customers with an older preview review it again. Support
staff can read the current policy and follow up on tickets from **Support inbox**.

## Start the backend

Prerequisites: Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
cd backend
uv sync --frozen
test -f .env || cp .env.example .env
uv run alembic upgrade head
uv run python -m canopy seed --as-of 2026-11-24
uv run uvicorn canopy.api:create_app --factory --reload --host 127.0.0.1 --port 8000
```

Open [API documentation](http://127.0.0.1:8000/docs).
`GET /health/live` checks the process; `GET /health/ready` checks database connectivity and migration revision.
SQLite is the development/test fallback. It does **not** reproduce PostgreSQL RLS.
Authentication requires Supabase; there is no unauthenticated demo bypass.

## Hosted development configuration

The maintainer's development backend has been verified against hosted Supabase. The public repository
contains no usable hosted account credentials or deployment configuration. To run your own environment,
use local Supabase below or configure your project following the authentication section.

Machine-specific cloud identity is stored in ignored `supabase/cloud.json`. Server settings, synthetic
account credentials, local fallback configuration, and bootstrap state remain ignored. Real hosted
sign-in, customer isolation, role checks, simulated refunds, escalation, and forced RLS have passed.
No frontend/backend application host is deployed yet.

The local setup command writes local settings into `backend/.env`; restart the API after switching
environments. Never copy a maintainer's private settings into a clone or into CI.

## Run Supabase locally

The project has an isolated Supabase configuration on ports 54331–54334, separate from other
local Supabase projects. Phase 1 uses PostgreSQL 15 and a minimal Auth/API stack.
The setup helper uses a project-specific Docker network and local connection URLs.
Start Docker Desktop, then run:

```sh
cd backend
uv sync --frozen
uv run python -m canopy.local_setup --start
```

The setup command starts the local stack, saves its connection settings to `backend/.env` while
preserving your Gemini key, applies Alembic migrations, seeds Amazora, creates three synthetic
customer accounts plus support/admin accounts, and verifies real Auth sessions and data isolation.
It only operates on this project's API at `127.0.0.1:54331` and database at `127.0.0.1:54332`.
It refuses hosted or unexpected endpoints. It never prints keys or account passwords.

Generated account credentials are in the ignored, permission-restricted `backend/.env.demo` file.
The command reuses existing synthetic accounts and credentials on subsequent runs.
These are local development accounts, not production users. Setup creates confirmed accounts directly
without sending email. Studio, storage, email testing, Realtime, edge functions, and analytics are
disabled until their phases need them. Run `supabase stop` from the project root when finished;
this preserves local data.
Do not use `supabase db reset` as a substitute for Alembic migrations.

| Local service | URL |
|---|---|
| Supabase API | `http://127.0.0.1:54331` |
| PostgreSQL | `127.0.0.1:54332` |

Ports 54333 (Studio) and 54334 (email inbox) are reserved in configuration but not started in Phase 1.

If `.env` already exists, edit it rather than copying over it.
All backend commands below run from `backend/`.

## Configure Gemini

Create an API key in [Google AI Studio](https://aistudio.google.com/api-keys), then edit `backend/.env`:

```dotenv
GEMINI_API_KEY=your-key
GEMINI_MODEL=gemini-3.5-flash-lite
```

Keep the key on the server. Do not use a `NEXT_PUBLIC_` variable or paste keys into chat.
Then run:

```sh
uv run python -m canopy check-config
uv run python -m canopy gemini-smoke
```

`check-config` shows presence flags, never credentials; it does not test connectivity.
`gemini-smoke` makes one small synthetic request and reports success and token counts.
It requires model access and quota in your Google account. A configured key alone does not verify them.
The adapter has a 20-second timeout, a 12,000-character input cap, a 512-token output cap,
no automatic tool calling, and no SDK retries. Text workflows and API query embeddings explicitly
retry known transient failures up to `PROVIDER_MAX_ATTEMPTS` (default 2), admitting every attempt
against shared quotas. Text turns share an 80-second provider budget; invalid output and business
actions are not retried. Direct CLI adapter calls make one attempt.
Thinking tokens count toward reported output usage.
Limits are configurable in `.env`. This limits a request; it is **not** a monthly spending cap.

Use synthetic data while prototyping with a free tier. See Google's current
[pricing and data-use terms](https://aistudio.google.com/docs/pricing).
No provider request is made at startup, during health checks, or by ordinary tests.

### Policy embeddings and citations

Phase 3 uses `gemini-embedding-001` with 768 normalized dimensions, independently of the chat model.
Run `uv run python -m canopy embedding-smoke`, then `policy-index` and `policy-evaluate` to build and
evaluate the approved Markdown corpus. Indexing is repeatable; unchanged sources make no provider calls.
The authenticated `POST /api/policies/search` endpoint returns source/version citations and explicit
missing/conflicting evidence. Configure provider data terms suitable for customer query text before
setting `POLICY_SEARCH_ENABLED=true`; it defaults to false. Public/synthetic CLI indexing and evaluation
do not require that flag. See [Phase 3 review](docs/phase-3-review.md) for model pricing/terms, commands,
response states, evaluation, and migration recovery.

### Support workflow

Configure provider data terms suitable for customer messages/order context before setting
`WORKFLOW_ENABLED=true`; it defaults to false. Enable policy search separately for cited evidence.
Create a conversation with `POST /api/conversations`, then send turns with a stable `Idempotency-Key`.
Refunds require a separate explicit confirmation of a specific expiring preview; chat consent alone
cannot submit one. Read [Phase 4 review](docs/phase-4-review.md) for JSON contracts, SSE behavior,
turn-boundary PostgreSQL memory, provider errors, token/cost baselines, evaluation, and recovery.
Optional metadata-only LangSmith export requires `WORKFLOW_TRACING_ENABLED=true` and a backend-only
`LANGSMITH_API_KEY`; automatic raw graph tracing stays disabled.

## Configure Supabase authentication and PostgreSQL

1. Create a Supabase project. Set `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` in `backend/.env`.
   The publishable key is used only to call Supabase Auth from the backend; it grants no application-data access.
2. Set `DATABASE_URL` to the project's direct PostgreSQL connection or **session** pooler connection
   using the `postgresql+psycopg://` driver. URL-encode special characters in the password.
   Keep all database credentials server-only. Use SSL as specified by your Supabase connection instructions.
3. Run `uv run alembic upgrade head` against the development Supabase project, then run the seed command.
4. Create a confirmed test account through Supabase Auth. Copy its user UUID, then link it to a seed customer:

```sh
uv run python -m canopy link-customer --customer maya --auth-user-id YOUR_AUTH_USER_UUID
```

The link command refuses to overwrite an existing different link. Seed customers initially have no
Auth identity and cannot sign in until linked. The Auth user UUID, rather than email, establishes ownership.

Customer is the default role. For a support or administrator account, assign `support` or `admin` in
the user's **app_metadata** through trusted Supabase administration. User-editable `user_metadata`
never grants permissions. Unknown roles are rejected.

The application SQL connection switches to Supabase's `service_role` before accessing data.
Migrations run as the database owner. All Canopy tables have RLS enabled and forced, no client policies,
and direct grants revoked from `anon`, `authenticated`, and `PUBLIC`.
Authorization is enforced in the backend before any customer-scoped query.
The PostgreSQL migration expects the standard Supabase roles; plain PostgreSQL requires equivalent
test-role bootstrap (the disposable CI test demonstrates this).

### Test a real sign-in

Use `/docs` to call `POST /api/auth/login` with the test account's email and password.
The backend verifies identity with Supabase, sets HttpOnly session cookies, and returns only safe identity fields.
Then call `GET /api/orders`. Sign in as a second linked customer and verify that the first customer's order
returns `404`. `POST /api/auth/refresh` rotates the session; `POST /api/auth/logout` clears cookies and
requests revocation of the refresh session. Already-issued access JWTs may remain valid until expiry.

The Next.js rewrite serves the frontend and API on the same origin. Configure explicit
`ALLOWED_ORIGINS` on the backend. Cookies are Secure in production and SameSite=Lax.
Untrusted-origin mutations are rejected. Never return Auth tokens to frontend JavaScript or localStorage.
Production requires HTTPS, a PostgreSQL connection with hostname and CA verification, an explicit
origin allowlist, and a shared admission secret. PostgreSQL enforces rate limits and daily AI call
budgets across replicas. See the [release runbook](docs/release.md) for deployment and recovery.

## Current API endpoints

| Method | Path | Access |
|---|---|---|
| GET | `/health/live` | Public process health |
| GET | `/health/ready` | Public database/migration health |
| POST | `/api/auth/login` | Password sign-in |
| POST | `/api/auth/refresh` | Refresh cookie |
| POST | `/api/auth/logout` | Clear/revoke current session |
| GET | `/api/auth/me` | Verified account |
| POST | `/api/policies/search` | Verified account; cited policy excerpts, when query embedding is enabled |
| POST/GET | `/api/conversations` | Create/list owned customer conversations |
| GET | `/api/conversations/{id}/messages` | Owned conversation history |
| GET | `/api/conversations/{id}/history` | Recent owned messages with stored policy citations |
| GET | `/api/conversations/{id}/quote` | Current owned unexpired refund preview |
| GET | `/api/conversations/{id}/executions` | Owned safe execution metadata |
| POST | `/api/conversations/{id}/turns` | Atomic support turn, when enabled; `Idempotency-Key` required |
| POST | `/api/conversations/{id}/turns/stream` | Progress plus validated response SSE |
| GET | `/api/orders` | Customer's own orders; bounded pagination |
| GET | `/api/orders/{id}` | Customer's own order, items, and shipment |
| GET | `/api/orders/lookup/{order_number}` | Customer's own order by number |
| GET | `/api/orders/{id}/tracking` | Customer's own shipment or explicit missing-tracking response |
| POST | `/api/refunds/eligibility` | Read-only item/quantity policy check |
| POST | `/api/refunds` | Confirmed simulated submission; requires `Idempotency-Key` |
| GET | `/api/refunds` | Customer's own requests |
| GET | `/api/refunds/{id}` | Customer's own refund status |
| POST | `/api/escalations` | Create a human-review ticket; requires `Idempotency-Key` |
| GET | `/api/escalations/{id}` | Customer's own ticket |
| GET | `/api/admin/refunds` | Support/admin refund review |
| GET | `/api/admin/escalations` | Support/admin ticket queue |
| PATCH | `/api/admin/escalations/{id}` | Support/admin assignment and status |
| GET | `/api/admin/summary`, `/api/admin/queue-metrics` | Support/admin aggregate counts |
| GET | `/api/admin/escalations/{id}/context` | Ticket, linked order, customer, recent messages and executions |
| GET | `/api/admin/refunds/{id}/context` | Refund, linked order and customer |
| GET | `/api/admin/executions` | Staff execution metadata and recorded stages |
| GET | `/api/policies/index` | Staff inspection of the approved corpus and index integrity |
| GET | `/api/policies/returns` | Support/admin current published return policy |
| GET | `/api/policies/returns/history` | Admin policy revision history |
| PUT | `/api/policies/returns` | Admin publication with expected revision; stale updates conflict |
| WebSocket | `/api/conversations/{id}/voice` | Owned call conversation; voice feature opt-in |

See [Phase 2 review](docs/phase-2-review.md) for business APIs and
[Phase 4 review](docs/phase-4-review.md) for the multi-turn support workflow.

## Synthetic dataset

Three fictional customers, four products, eleven orders, order items, and synthetic shipments.
Examples include an eligible-age delivery, a 30-day boundary, an expired return window, an in-transit order,
a gift card, an unpaid order, a cancelled order, and another customer's order.
The approved deterministic rules evaluate these scenarios; see [Amazora policy](docs/amazora-rules.md).

Seed UUIDs are deterministic and rerunning the command adds only missing records.
It preserves existing data, original dates, and Auth links. `--as-of` anchors dates on first creation.
Amounts use decimal database columns, never floating-point financial arithmetic.
There are no invented AI resolution metrics or fake execution traces.

## Tests

```sh
uv run --frozen pytest -q
```

Ordinary tests run real migrations on isolated temporary SQLite databases and mock external Auth/Gemini calls.
They cover ownership, role elevation attempts, cookies, validation redaction, upstream failures, seed
repeatability, database constraints, migration reversal, PostgreSQL SQL generation, Gemini limits,
refund policy boundaries, partial quantities, confirmation, rollback, idempotency, and escalation workflows.

The PostgreSQL integration test runs only when `POSTGRES_TEST_URL` points to a **dedicated disposable**
database. The tests create roles, migrate, seed, and verify runtime roles, RLS, client denial,
concurrent refund retries/reservations, and composite ownership constraints.
Never set that variable to a shared or production database. GitHub Actions provisions an isolated PostgreSQL
service with pgvector and runs these checks. Consult GitHub Actions for current workflow results.
The local setup command separately verifies real local Supabase authentication, refresh/logout,
cross-customer denial, role restrictions, direct REST database denial, and forced PostgreSQL RLS.
Phase 2 verification includes the full suite against a fresh disposable PostgreSQL database; see
[Phase 2 review](docs/phase-2-review.md) for the final results. Real local and hosted Auth verification
have also passed.
Phase 3 adds index atomicity/concurrency, corpus integrity, evidence abstention, citation versions,
embedding failures, real pgvector search, and Supabase extension-schema compatibility tests.

## Project layout

```text
backend/
  canopy/              API, Auth, models, rules, business tools, Gemini adapter, seed CLI
  migrations/          Versioned schema and PostgreSQL access restrictions
  tests/               Backend/policy tests and optional live PostgreSQL integration
  .env.example         Server configuration template
  pyproject.toml       Phase-specific dependencies
  uv.lock              Reproducible dependency resolution
docs/
  plan.md              Phases and review gates
  architecture.md      Data and identity boundaries
  amazora-rules.md      Approved versioned policy rules
src/                   Next.js customer and staff workspaces
tests/e2e/             Browser workflows against the disposable FastAPI test server
```

LangGraph and LangSmith are backend-only dependencies; no frontend provider keys are used.

## Repository workflow and handoff

Read [CONTRIBUTING.md](CONTRIBUTING.md) for local hooks, Conventional Commits, pull requests, and
checks. [Repository workflow](docs/repository-workflow.md) explains protected branches, CI, dependency
updates, and release checks. Report vulnerabilities through [SECURITY.md](SECURITY.md).
The owner has deferred choosing a license; no open-source license is included yet.

For the next development session, start with [docs/HANDOFF.md](docs/HANDOFF.md) and `AGENTS.md`.
Machine-specific operational notes stay in ignored `.local/HANDOFF.md` on the maintainer's machine.
