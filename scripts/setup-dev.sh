#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
uv sync --project backend --frozen
git config --local core.hooksPath .githooks
uv run --project backend --frozen python scripts/repo_tools.py gitleaks version
uv run --project backend --frozen python scripts/repo_tools.py actionlint -version
printf '%s\n' 'Development tools and repository-local commit hooks are ready.'
