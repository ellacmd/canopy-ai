# Phase 6 release review

Phase 6 adds shared PostgreSQL rate limits and daily Gemini quotas, production configuration checks,
trace delivery status, and a portable release with explicit migration and rollback steps. The release
build uses Next.js and FastAPI containers behind Caddy with managed Supabase for data and Auth.

## Local evidence

| Check | Measured result |
|---|---|
| Backend suite | 166 passed using a fresh PostgreSQL 17.12 database with pgvector 0.8.7 |
| Shared admission | Two PostgreSQL engine pools competed for 7 reservations. Exactly 7 of 32 attempts succeeded. Client roles were denied access. |
| Browser | 3 Chromium scenarios passed against the production frontend build and a migrated FastAPI test database. They exercised refund replay, staff workflow, quota exhaustion, and coordinated session refresh. |
| HTTPS release smoke | Native Caddy, built Next.js, and the production FastAPI configuration passed over local HTTPS and verify-full PostgreSQL. The API used a separate database login and switched to `service_role`. Supabase Auth and Gemini responses were simulated. |
| Recovery | A local backup restored into a fresh database at revision `0006`. Alembic reported no drift. The runtime role read 11 synthetic orders, 1 committed turn, and 11 admission counters from the restore. |
| Build and repository checks | Backend wheel, frontend build, TypeScript, Ruff, actionlint, public-file guard, Gitleaks, Compose and Caddy validation, and Python/npm advisory checks passed locally. |

The required CI image job is configured to build and launch the containers against disposable
PostgreSQL. Local HTTPS verification used native processes. The container job needs a Docker runner.
See [release](release.md) for the host setup, repeatable smoke test, and recovery procedure.

## Live model evidence

The latest captured provider baseline is the Phase 4 run. It passed **25 of 36** cases, with 11
unavailable after provider and network errors. Median active turn latency was **2.67 seconds**.
Reported token usage cost an estimated **$0.00736670** in chat charges across all attempts, excluding
embedding charges and failed requests with missing usage. Those measurements came from the Phase 4
build with pacing and retry settings recorded in its report. [Phase 4 review](phase-4-review.md) ·
[Raw evaluation report](evaluations/phase-4-workflow.json)

The new release fixture adds 13 cases for exact citations, owned and foreign orders, explicit
confirmation, replay, and escalation. The runner records the fixture hash, provider availability,
turn and provider latency percentiles, estimated chat cost, and incomplete chat usage.

```sh
# Set WORKFLOW_EVAL_DATABASE_URL to a fresh disposable loopback PostgreSQL database
# named canopy_workflow_eval*. Load the Gemini key from the approved credential store.
uv run --project backend --frozen python -m canopy workflow-evaluate --suite baseline > baseline.json
# Use a second fresh evaluation database for the release fixture.
uv run --project backend --frozen python -m canopy workflow-evaluate --suite release > release.json
```

The hosted release gate remains open. It needs an approved host and HTTPS domain, a configured
Supabase staging environment, live Gemini runs for both fixtures, and successful LangSmith delivery
verified by trace ID. The release fixture has no measured provider accuracy, latency or cost yet.
