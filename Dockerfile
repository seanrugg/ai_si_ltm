# syntax=docker/dockerfile:1
# LTMS - Long-Term Memory Server
# Multi-stage image: build the dashboard with Node, then run the Python app.
# Configuration comes entirely from environment variables at run time; no
# secrets, hostnames or site values are baked in (see AGENTS.md rule 10).
#
# Build:
#   docker build -t ltms:dev .
# Run (sqlite, for a quick local check):
#   docker run --rm -e LTMS_DATABASE_URL=sqlite+aiosqlite:///./dev.db \
#     -e LTMS_ADMIN_PASSWORD=dev-password-123 -e LTMS_COOKIE_SECURE=false \
#     -p 8080:8080 ltms:dev
#
# Run (Postgres, as in deploy/):
#   docker run --rm -e LTMS_DATABASE_URL=postgresql+asyncpg://ltms:change-me@db:5432/ltms \
#     -e LTMS_ADMIN_PASSWORD=change-me -e LTMS_COOKIE_SECURE=false \
#     -p 8080:8080 ltms:dev

# Stage 1: build the dashboard (same as deploy/plesk-deploy.sh: npm ci + npm run build).
FROM node:20-bookworm-slim AS dashboard
WORKDIR /app/dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci
COPY dashboard/ ./
RUN npm run build

# Stage 2: run the LTMS server (same uvicorn flags as deploy/ltms.service.template,
# listening on 0.0.0.0:8080 inside the container; uvicorn reads FORWARDED_ALLOW_IPS).
FROM python:3.12-slim-bookworm

RUN useradd -r -u 10001 -U ltms

WORKDIR /app
ENV LTMS_DASHBOARD_DIR=/app/dashboard/dist
ENV PYTHONUNBUFFERED=1
# Which proxy addresses uvicorn trusts for X-Forwarded-For (client IPs, rate limits).
# Set it to your reverse proxy's address as seen from the container.
ENV FORWARDED_ALLOW_IPS=127.0.0.1

COPY --from=dashboard /app/dashboard/dist /app/dashboard/dist
COPY . .

# aiosqlite is not a declared dependency of the package, but the documented
# smoke test (docker run with LTMS_DATABASE_URL=sqlite+aiosqlite:///./dev.db)
# needs it; a real deployment overrides LTMS_DATABASE_URL with Postgres.
RUN pip install --no-cache-dir . "aiosqlite>=0.20"

# /app must be writable by the runtime user (e.g. a sqlite dev database at ./dev.db).
RUN chown -R ltms:ltms /app

USER ltms

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=2)"

# One worker on purpose: rate limits live in process memory.
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--proxy-headers", "--no-server-header", "--log-level", "info"]
