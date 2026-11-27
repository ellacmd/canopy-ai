# Canopy AI release runbook

Canopy runs as Next.js, FastAPI, and Caddy containers on a host with Docker Compose. Caddy terminates
HTTPS and routes both workspaces and the API through one origin. Only the gateway publishes ports.
Supabase supplies PostgreSQL with pgvector and Auth. Staging and production each need their own
Supabase project, hostname, runtime credentials, and release configuration.

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

Revision `0006` adds the forced-RLS counter table. The API readiness check requires that exact
revision, so schedule a brief traffic pause during cutover from `0005`. Reopen after both health
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

## Usage and retention

| Control | Default |
|---|---:|
| Login transactions per minute | 10 per email, 40 per source IP, 300 overall |
| Refresh transactions per minute | 90 per source IP, 900 overall |
| New workflow turns per minute | 15 per verified customer identity |
| Gemini attempts per minute | 16 per verified Auth identity across chat and embedding |
| Gemini attempts per UTC day | 500 chat, 250 query embedding |

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
unreported usage. Bound overall vendor spend in the provider project when setting the quotas.

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
2. If the issue is the app build, restore the previous compatible image tags. For a rollback to the
   `0005` app, export counters if needed for incident evidence, then use the `0006` migration artifact
   and the owner connection to run `alembic downgrade 0005`. This drops active quotas and leaves
   refund and replay tables in place. The old app uses process-local login limits, so keep ingress
   restricted until the current release is restored.
3. If the data needs recovery, restore into an isolated database or Supabase project first. Verify the
   extension, revision, role grants, forced RLS, customer links, policy index, refund reservations, and
   existing turn replays. Reset custom role passwords as required by the provider. Confirm Auth
   account IDs and app metadata, since ownership depends on those IDs. Reconcile any actions
   between the snapshot and incident before exposing the restored copy.
   [Supabase backups](https://supabase.com/docs/guides/platform/backups) ·
   [Supabase project restore](https://supabase.com/docs/guides/platform/migrating-within-supabase/backup-restore)
4. Run schema checks, HTTPS smoke and the staging browser scenarios before reopening traffic.
   Record recovery elapsed time and the verified snapshot point.

