# Working on Canopy AI

Read `docs/HANDOFF.md`, `docs/plan.md`, and `docs/amazora-rules.md` before implementation.
The owner requested phased, backend-first work. Phases 1–2 are complete; UI is paused.
Do not start another phase unless requested. Do not delegate to other agents unless requested.

## Data and safety boundaries

- All business data access is through the authenticated backend. No browser Supabase CRUD.
- Preserve forced RLS and zero client policies/grants. Runtime SQL uses `service_role`.
- Derive customer identity from verified Auth, never request/model fields. Filter every customer query.
- Refunds remain simulated; use deterministic rules, explicit confirmation, atomic reservations,
  customer-scoped idempotency, and audit records. Models cannot authorize financial actions.
- Never print, commit, upload, or copy credentials from `.env*`, local account files, bootstrap files,
  or credential stores. Read configuration privately when necessary. Never use live credentials in CI.
- `.local/` contains ignored machine-specific handoff notes. Public docs must use placeholders for
  deployment/account identifiers and must not include personal paths or operational credentials.

## Verification and workflow

From the repository root:

```sh
uv sync --project backend --frozen
uv run --project backend ruff check backend scripts
uv run --project backend ruff format --check backend scripts
uv run --project backend pytest -q backend/tests
python3 scripts/public_guard.py
uv run --project backend --frozen python scripts/repo_tools.py gitleaks git --redact --log-opts=--all
```

PostgreSQL tests require a **dedicated disposable** `POSTGRES_TEST_URL`. Never use the active
cloud database for that variable. Do not reset the hosted database or regenerate the seed dates.
Run required checks for the affected code, and report actual results; do not claim GitHub CI ran
until a remote run has completed. Frontend build/E2E checks are deferred while `src/` is empty.

Use focused Conventional Commits and short-lived branches. Follow `CONTRIBUTING.md` and
`docs/repository-workflow.md`. Keep lockfiles synchronized. Do not push, publish, deploy, change
repository visibility, or alter an account's global Git settings without authorization. Do not amend
or rewrite shared history. Machine-local hooks are installed with `scripts/setup-dev.sh`.
