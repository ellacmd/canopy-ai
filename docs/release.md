# Canopy AI release runbook

Canopy runs as Next.js, FastAPI, and Caddy containers on a host with Docker Compose. Caddy terminates
HTTPS and routes both workspaces and the API through one origin. Only the gateway publishes ports.
Supabase supplies PostgreSQL with pgvector and Auth. Staging and production each need their own
Supabase project, hostname, runtime credentials, and release configuration.

## What is needed to host and release

| Requirement | Release decision or evidence |
|---|---|
| Application host | Linux host with Docker Engine/Compose and persistent disk. Start staging at 2 vCPU / 4 GB RAM as a planning estimate; measure memory and concurrent voice load before sizing production. |
| Domain and network | DNS access, HTTPS hostname per environment, inbound TCP 80/443, outbound Supabase/Gemini/LangSmith access. |
| Supabase | Separate staging/production projects with Auth and pgvector, owner migration credentials, restricted runtime login, trusted CA. Source head is `0008`. |
| Gemini | Working chat, embedding and optional Live model access; suitable data-processing terms, quota and provider spend controls. |
| Secrets | Backend-only database/Auth/provider settings, random shared admission key, restricted deployment environment files. |
| Trace verification | LangSmith key/project when shipping trace export; inspect a submitted trace and confirm metadata-only content. Otherwise keep export disabled and record the deferred gate. |
| Recovery | Recoverable off-host backup and a tested restore of the current schema, Auth links, policy history and replay records. |
| Operations | Named incident owner, external readiness/HTTPS alerts, safe error and quota monitoring, daily admission cleanup, agreed retention. |
| Release evidence | Green CI for the reviewed source, current baseline/release provider evaluations, staging HTTPS/browser checks, and live voice verification when voice ships. |

The existing Compose deployment hosts the complete application. Voice requires a persistent FastAPI
process and WebSocket-capable ingress; a frontend-only host does not provide that backend.
The starting host size above is an estimate, not a measured production capacity commitment.
Supabase recommends off-site exports for Free projects; do not assume the development project's plan
provides a recoverable production backup. [Supabase backup documentation](https://supabase.com/docs/guides/platform/backups)

## Configure the environment

1. Point a DNS hostname at the release host and allow TCP 80 and 443. The reference topology places
   Caddy directly at the edge. It discards incoming client forwarding headers and FastAPI trusts
   only Caddy's private address. If an upstream proxy is added, configure its trusted IPs in Caddy
   before relying on per-IP limits. [Caddy reverse proxy behavior](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)
2. Copy `deploy/.env.example` to `deploy/.env`. Set the hostname, a release tag such as the reviewed
   commit SHA, and the trusted Supabase CA certificate file path. The Compose file mounts the
   certificate at `/run/secrets/supabase-ca.crt`. Make the public CA file readable by the non-root
   backend user.
3. Provision the owner migration connection and a separate runtime login authorized to
   `SET ROLE service_role`. Give the runtime login the provider-approved membership needed
   for that role and verify it cannot perform schema-owner operations. Use a direct connection or
   session pooler on port 5432. Use `sslmode=verify-full` with the Supabase CA.
   [Supabase database connections](https://supabase.com/docs/guides/database/connecting-to-postgres) ·
   [Supabase TLS verification](https://supabase.com/docs/guides/platform/ssl-enforcement)
4. Copy `backend/.env.example` to a restricted `backend/.env` on the release host. Supply the
   runtime database URL, Supabase HTTPS origin and publishable key, and a random `ADMISSION_KEY`
   of at least 32 characters shared across backend replicas. Compose supplies the single HTTPS
   application origin. Protect the owner credential separately and load it only for migrations.
5. Provision synthetic staging customer and staff accounts. Link the customer profiles and set
   the staff role through trusted Supabase Auth app metadata. Enable Gemini only after confirming
   the data terms for that project, configuring the API key, and indexing the approved corpus.
   Enable trace export only with the intended LangSmith project key and workflow enabled.
6. Set `PROVIDER_MAX_ATTEMPTS=2`, `GEMINI_TIMEOUT_SECONDS=20`, and
   `WORKFLOW_PROVIDER_BUDGET_SECONDS=80`, or use explicitly evaluated alternatives. Every retry
   consumes provider admission; there are no hidden SDK retries. Enable `VOICE_ENABLED` separately
   only after approving audio processing and verifying `GEMINI_LIVE_MODEL` access.

## Pass the live reliability gate

Use a fresh disposable loopback PostgreSQL/pgvector database for each suite, with the standard
Supabase-equivalent test roles. Never use the shared cloud database. Load its URL into
`WORKFLOW_EVAL_DATABASE_URL` and the Gemini key privately. Run with the intended release's runtime
retry, timeout and budget settings, from the reviewed source. Explicit whole-turn evaluation retries
must remain disabled, so the evidence measures the runtime behavior customers receive.

```sh
uv run --project backend --frozen python -m canopy workflow-evaluate \
  --suite baseline --max-attempts 1 > baseline.json
# Switch WORKFLOW_EVAL_DATABASE_URL to a second fresh disposable evaluation database.
uv run --project backend --frozen python -m canopy workflow-evaluate \
  --suite release --max-attempts 1 > release.json
# Example initial p95 target: 30 seconds. Agree and record the target before running.
uv run --project backend --frozen python scripts/check_release_evaluations.py \
  --baseline baseline.json --release release.json --max-p95-ms 30000
```

The checker requires 36/36 baseline and 13/13 release cases, zero unavailable cases, matching current
fixture hashes and runtime settings, one turn attempt, and p95 within the chosen limit. It rejects
missing or malformed evidence and exits nonzero on failure. Preserve both raw reports, the source
commit and dirty-state information, provider project tier, settings, CI run, and timestamps with the
release record. The reports do not prove voice reliability: run the voice staging checks separately.
The historical 25/36 report remains failed evidence until replaced by fresh passing runs.

## Deploy a reviewed release

Run from the repository root on the release host. Record the commit, both image IDs, the gateway
image digest, database revision, and previous release tag. Verify a recoverable backup before
migrating. GitHub Actions builds and checks images using disposable
services. It has no deployment credentials.

```sh
docker compose --env-file deploy/.env config --quiet
docker compose --env-file deploy/.env build backend web
# Load MIGRATION_DATABASE_URL privately from the approved credential store.
DATABASE_URL="$MIGRATION_DATABASE_URL" docker compose --env-file deploy/.env run --rm --no-deps \
  -e DATABASE_URL backend alembic upgrade head
DATABASE_URL="$MIGRATION_DATABASE_URL" docker compose --env-file deploy/.env run --rm --no-deps \
  -e DATABASE_URL backend alembic check
docker compose --env-file deploy/.env up -d --no-build --wait
```

Revision `0006` adds the forced-RLS counter table, `0007` adds conversation channels, and `0008`
adds forced-RLS published policy history. The API readiness check requires **`0008`**, so schedule
a traffic pause and drain active calls/turns before migration. Reopen after both health
checks and a smoke test pass. Liveness checks only the process. Readiness checks the revision,
database access, and runtime admission table. Provider access and trace export have a separate
staging smoke test.

```sh
# Load CANOPY_SMOKE_BASE_URL and CANOPY_SMOKE_{CUSTOMER,SECOND_CUSTOMER,SUPPORT}_{EMAIL,PASSWORD}
# from the synthetic staging credential store.
uv run --project backend --frozen python scripts/release_smoke.py --workflow --require-trace
```

The script checks HTTPS, cookies, refresh, role boundaries, a cross-customer order denial, a
cited policy turn, same-key replay, and staff trace visibility. `submitted` records the outcome
of the LangSmith client request. Look up the returned trace ID in the configured project and
check that its payload contains execution metadata only. In Chromium, additionally run a
confirmed simulated refund and verify same-key replay produces one request, then review its
context and a synthetic escalation as staff.

Also verify on staging:

- Admin publication and history; support read access and denied publication; a stale preview must
  be reviewed again before confirmation. Preserve the approved policy when ending the smoke test.
- Voice enabled: HTTPS microphone permission, WebSocket authentication, two-way audio, interruption,
  mute/end, call transcript isolation, cited lookup, explicit refund confirmation, and staff handoff.
  A network interruption must end safely without duplicating a submitted action. Voice reconnect
  is manual; do not add automatic session replay.
- Both readiness and recovery at schema `0008`, using the packaged release images. A container
  health check marks a process unhealthy but does not itself restart an unhealthy live process;
  alert an operator and test the host's recovery procedure.

Caddy supports WebSockets and flushes SSE responses automatically. Preserve that behavior in any
upstream proxy. Active WebSockets close on Caddy config reload by default; drain calls before release.
[Caddy streaming behavior](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy#streaming)

## Usage and retention

| Control | Default |
|---|---:|
| Login transactions per minute | 10 per email, 40 per source IP, 300 overall |
| Refresh transactions per minute | 90 per source IP, 900 overall |
| New workflow turns per minute | 15 per verified customer identity |
| Gemini attempts per minute | 16 per verified Auth identity across chat and embedding |
| Gemini attempts per UTC day | 500 chat, 250 query embedding |
| Live call starts per UTC day | 100 |
| Maximum voice call duration | 300 seconds |
| Explicit attempts per chat/query-embedding call | 2, including the first |
| Shared provider budget per text turn | 80 seconds |

PostgreSQL time defines fixed windows. A refused request returns HTTP `429` with `Retry-After`.
If a stream has started it emits a corresponding SSE error and retry time. A committed same-key
turn replay skips rate and provider admission. Explicit actions use the turn limit and require
no chat calls. A policy lookup embedded in chat falls back to missing evidence if the embedding
budget is exhausted. Direct policy search returns `429` when its budget is exhausted.

Provider counters commit before the attempt and include timeouts, bad responses, disconnected
clients, and requests where domain writes roll back. Each operation has a separate deployment-wide
limit. Setting one to zero pauses that operation. A shared project key can accrue usage elsewhere,
and operator-run policy indexing and evaluations are separate. Budget limits count attempts.
Cost reports estimate chat token charges using the recorded rate card and exclude embeddings and
unreported usage, including live-audio charges. Live call admission counts starts, not duration or
tokens. Bound overall vendor spend in the provider project when setting the quotas.

Schedule `docker compose --env-file deploy/.env exec -T backend python -m canopy prune-admission`
daily from the release host's scheduler. It removes counters at least one day after their window
ends. Account and IP subjects are keyed hashes. Rotating the shared secret resets the active
subject windows and preserves the global limits. The cleanup leaves conversation, refund,
replay, checkpoint, support, and audit records available for recovery. Set a domain-data retention
policy with the owner before deleting these records.

## Recover or roll back

1. Pause ingress. Record the failing release, database revision, and safe request or trace IDs.
   For a provider incident, use the workflow feature flags or zero budget and check deterministic
   order, refund, and escalation routes.
2. If the issue is the app build, restore only image tags verified to support the **current schema**.
   Readiness checks pin the revision: older `0006`/`0007` applications will not serve a `0008`
   database. Prefer a forward fix or restore the backup into an isolated environment with its
   matching app images. Do not blindly downgrade: reversing `0008` destroys policy history,
   reversing `0007` destroys chat/call separation, and reversing `0006` drops active quota state.
   Any schema downgrade needs a reviewed data-preservation plan and a restore rehearsal first.
3. If the data needs recovery, restore into an isolated database or Supabase project first. Verify the
   extension, revision `0008`, role grants, forced RLS, customer links, published policy history,
   conversation channels/transcripts, policy index, refund reservations, and existing turn replays.
   Reset custom role passwords as required by the provider. Confirm Auth
   account IDs and app metadata, since ownership depends on those IDs. Reconcile any actions
   between the snapshot and incident before exposing the restored copy.
   [Supabase backups](https://supabase.com/docs/guides/platform/backups) ·
   [Supabase project restore](https://supabase.com/docs/guides/platform/migrating-within-supabase/backup-restore)
4. Run schema checks, HTTPS smoke and the staging browser scenarios before reopening traffic.
   Record recovery elapsed time and the verified snapshot point.

