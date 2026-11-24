# Contributing

Canopy AI is developed in reviewed phases. Read [the plan](docs/plan.md) and
[the current handoff](docs/HANDOFF.md) before proposing work. UI implementation is paused.
The project owner has not selected a license yet; no open-source license is included.

## Development setup

Use Python 3.12+, Git, and [uv](https://docs.astral.sh/uv/). From the repository root:

```sh
scripts/setup-dev.sh
git switch -c feat/short-description
```

Setup installs locked backend dependencies and repository-local hooks. The Gitleaks and actionlint
helpers download official release archives, verify pinned SHA-256 checksums, and cache the executables
under ignored `.cache/`. macOS and Linux on Intel/ARM are supported. No global Git configuration changes
are needed. Each contributor must run setup after cloning; Git does not install hooks automatically.

## Commits and pull requests

Make small commits describing one coherent change, including its relevant tests and documentation:

```text
feat(retrieval): index versioned return policies
fix(refunds): reject excess partial quantities
test(auth): cover expired sessions
docs: explain local setup
ci: pin updated actions
```

The local commit-message hook enforces Conventional Commit subjects. Pre-commit checks the staged
index for private files, scans staged changes for secrets, and runs Ruff and actionlint. Hooks help
locally; GitHub's required checks enforce the merge gate. Tests also verify that force-added private
files and credentials copied into ordinary source files are rejected by the index guard.

Push the feature branch and open a pull request to `main`. Explain the behavior and validation for
someone who has not read the chat. Required CI must pass, and review threads must be resolved.
Use squash merge with a Conventional Commit PR title. Do not commit directly to protected `main`,
force-push shared branches, merge failing checks, or put credentials in PR descriptions or logs.
For a solo maintainer, an additional approving reviewer is not required; add that requirement when a
second maintainer can review. CODEOWNERS identifies the owner but does not imply a review occurred.

## Required checks

```sh
uv run --project backend --frozen ruff check backend scripts
uv run --project backend --frozen ruff format --check backend scripts
uv run --project backend --frozen pytest -q backend/tests
python3 scripts/public_guard.py
uv run --project backend --frozen python scripts/repo_tools.py actionlint
uv run --project backend --frozen python scripts/repo_tools.py gitleaks git --redact --log-opts=--all
uv run --project backend --frozen pip-audit --local --skip-editable
npm audit --package-lock-only --audit-level=high
```

Ordinary tests skip the two PostgreSQL integration checks unless `POSTGRES_TEST_URL` points to a
dedicated disposable database. CI creates one and runs the full suite on Python 3.12/PostgreSQL 15
and Python 3.13/PostgreSQL 17. No Supabase/Gemini account or repository secret is required for CI.
External provider calls are mocked in the test suite. Real cloud verification is a separate explicit step.
The parked frontend has no application routes yet, so its build/E2E tests are deferred; its dependency
lockfile is still audited.

Update `backend/uv.lock` with Python dependency changes. Update `package-lock.json` with npm changes.
Review Dependabot PRs normally; automatic merging is disabled. When updating Gitleaks/actionlint,
verify official releases and update versions and checksums in `scripts/tool-versions.json` together.

## Security and releases

Never commit `.env` files, demo credentials, private notes, cloud bootstrap state, database dumps, real
customer data, or keys. `.env.example` contains blank/placeholder configuration only. If a secret is
published, revoke/rotate it promptly; deleting a file does not remove its history. See [SECURITY.md](SECURITY.md).

All refund writes must remain simulated and preserve ownership, confirmation, deterministic policy,
transactional reservations, idempotency, and audit records. Follow the approved rules.
Database migrations need compatibility and recovery notes; never reset the shared cloud database.

See [repository and release workflow](docs/repository-workflow.md) for branch settings and delivery plans.
