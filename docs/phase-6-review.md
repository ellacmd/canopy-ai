# Phase 6 release review

Phase 6 adds shared PostgreSQL rate limits and daily Gemini quotas, production configuration checks,
trace delivery status, and a portable release with explicit migration and rollback steps. The release
build uses Next.js and FastAPI containers behind Caddy with managed Supabase for data and Auth.

## Current source and release status — 27 November 2026

Source head is **`0008`**, including separate call/text conversations and versioned policy publication.
The table below records the original Phase 6 verification, not new proof for the current revision.
Text workflows and API query embeddings now use bounded explicit transient retries (2 attempts by
default); each attempt consumes quota and text turns share an 80-second provider budget. SDK retries
remain disabled. Invalid responses, business actions and live voice sessions are not automatically retried.

`scripts/check_release_evaluations.py` rejects stale fixture hashes, missing/failed cases, unavailable
cases, evaluation-only whole-turn retries, inconsistent runtime settings, and p95 beyond the selected
target. It requires all 36 baseline and 13 release cases. Commands and hosting requirements are in
[release](release.md). New deterministic tests cover retry recovery, quota enforcement, deadlines,
usage uncertainty, committed replay, and rejection of incomplete release evidence.

**The live release gate is still open.** The 25/36 provider baseline below predates these changes;
passing synthetic tests cannot replace new live runs. Current-revision hosted HTTPS, voice, policy
publication and backup/restore checks also remain required. LangSmith is needed only when releasing
trace export; otherwise leave export disabled and record that decision.

Current-change verification on 27 November 2026:

| Check | Result |
|---|---|
| Backend | 190 passed, 4 PostgreSQL integration tests skipped; no dedicated disposable PostgreSQL URL was configured. |
| Retry and gate coverage | Recovery, per-attempt admission, exhausted quota, bounded deadlines, embedding retries, usage uncertainty, committed replay, stale/missing evidence and latency limits passed. |
| Frontend | TypeScript passed. Production build passed with `next build --webpack`. Default Turbopack was blocked by this workspace's internal port-binding restriction; the default build still needs CI verification. |
| Source checks | Ruff lint/format, whitespace checks, public-file guard, actionlint and full-history Gitleaks passed locally. Remote CI was not run in this session. |
| Backend package | Locked, offline wheel build passed using the configured Python 3.12 interpreter. |
| Historical evidence rejection | The release checker returned nonzero for the existing 25/36 report, as required. |
| Live Gemini | Synthetic smoke failed with safe category `provider_ConnectError`; no fresh passing provider evaluation is claimed. |

Docker image execution, hosted deployment, current-schema PostgreSQL integration/restore, and live
voice verification were not completed in this workspace. These remain release gates, not inferred passes.

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
uv run --project backend --frozen python -m canopy workflow-evaluate --suite baseline --max-attempts 1 > baseline.json
# Use a second fresh evaluation database for the release fixture.
uv run --project backend --frozen python -m canopy workflow-evaluate --suite release --max-attempts 1 > release.json
```

The hosted release gate remains open. It needs a selected host and HTTPS domain, a configured
Supabase staging environment, live Gemini runs for both fixtures, and successful LangSmith delivery when trace export ships,
verified by trace ID. The release fixture has no measured provider accuracy, latency or cost yet.
