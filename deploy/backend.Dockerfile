FROM ghcr.io/astral-sh/uv:0.10.10 AS uv
FROM python:3.12-slim AS build
COPY --from=uv /uv /bin/uv
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/canopy ./canopy
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12-slim
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --from=build /app/.venv ./.venv
COPY backend/alembic.ini ./alembic.ini
COPY backend/migrations ./migrations
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "canopy.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--limit-concurrency", "80", "--ws-max-size", "4096", "--ws-max-queue", "8", "--timeout-graceful-shutdown", "95"]
