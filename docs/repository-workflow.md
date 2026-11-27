# Repository and delivery workflow

The repository is intended to be public at `ellacmd/canopy-ai`. The owner deferred the license decision;
do not add MIT or another license until the owner chooses one. Public visibility and license selection
are separate choices. Development remains backend-first and phase-based.

## Git and review

Use `main` as the stable branch and short-lived `feat/`, `fix/`, `docs/`, or `chore/` branches.
Use Conventional Commits, one coherent change per commit, and squash-merge reviewed PRs.
Set a repository-local GitHub noreply email before committing to keep a personal email out of public
history. Local setup installs checks via `core.hooksPath=.githooks`; it does not change global settings.

The initial publication is a baseline commit of the already completed Phases 1–2. It does not invent
historical commits or imply that earlier work used PRs. After publication, protect `main` with:

- PRs required; no extra approval count for the current solo maintainer.
- The exact status check `Required CI` required, with the branch up to date.
- Resolved review conversations and linear history required.
- Administrator enforcement enabled; force pushes and deletion blocked.
- Squash merging enabled; merge commits/rebase merging disabled; merged feature branches deleted.

When another maintainer joins, require at least one independent approval and consider required
CODEOWNER reviews. Do not require a solo author to approve their own PR.

## CI

`.github/workflows/ci.yml` runs for every PR, pushes to `main`, manual dispatch, and a weekly schedule.
It intentionally has no path filters, so documentation-only changes still produce the required check.

| Check | What it validates |
|---|---|
| Quality and secrets | Conventional PR title, Ruff lint/format, actionlint, private-file guard, full-history Gitleaks |
| Backend tests | Complete suite with disposable PostgreSQL on Python 3.12/PG15 and Python 3.13/PG17; wheel build |
| Frontend build and browser tests | Next.js production build, TypeScript, and Chromium against disposable FastAPI |
| Release images and schema | Compose and Caddy configuration, image builds, packaged migration, schema drift, and image startup |
| Dependency audit | Locked Python packages and npm lockfile advisories |
| Required CI | Fails if any required job fails, is cancelled, or is skipped |

Actions are pinned to immutable commit SHAs. Workflow permissions are read-only, checkout does not
persist credentials, jobs have timeouts, and redundant runs are cancelled. PR workflows use
`pull_request`, and no cloud credentials are supplied. No workflow runs code from a PR with a
privileged deployment token. Gitleaks/actionlint downloads use checked-in checksums and extract only
the expected executable. Secret scans redact findings.

Dependabot checks uv, npm, and GitHub Actions weekly. Updates arrive as PRs and pass the same gates.
No automatic merging is configured. Advisory data can change even without source changes, so a weekly
audit may require a dependency update.

GitHub settings to verify after publishing: Dependabot vulnerability alerts/security updates, secret
scanning, push protection, and private vulnerability reporting. Settings require repository admin access;
files alone do not activate branch protection or account-level security features.

## Delivery and deployment

CI builds the backend wheel and checks the frontend and backend release images against disposable
services. The [release runbook](release.md) covers the reviewed Compose artifacts, staging, explicit
owner migrations, and rollback. Hosted release needs a selected host, HTTPS domain and isolated
Supabase configuration. Keep runtime and migration credentials separate. The current GitHub workflow
uses no cloud credentials and performs no live migrations.

## References

- [GitHub Actions security guidance](https://docs.github.com/en/actions/reference/security/secure-use)
- [Protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
- [Dependabot supported ecosystems](https://docs.github.com/en/code-security/reference/supply-chain-security/supported-ecosystems-and-repositories)
- [Gitleaks](https://github.com/gitleaks/gitleaks)
