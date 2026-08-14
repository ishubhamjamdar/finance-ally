# syntax=docker/dockerfile:1
#
# FinAlly — one image, one process, one port (PLAN.md §3, §11).
#
# Stage 1 builds the Next.js static export with Node. Stage 2 installs the
# Python dependencies with uv and copies that export in beside the backend.
# Node never reaches the runtime image; what survives is a directory of static
# files.

# ---------------------------------------------------------------------------
# Stage 1 — the frontend export
# ---------------------------------------------------------------------------
FROM node:20-slim AS frontend

WORKDIR /build

# The manifest and lockfile alone, so editing a component does not invalidate
# the install layer. `npm ci` and not `npm install`: the lockfile is the input,
# and a build that resolves its own versions is not the build that was tested.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

# PLAN.md §5: NEXT_PUBLIC_API_BASE is inlined into the bundle at build time,
# not read at runtime. Empty is the production value — the export and the API
# are served from the same origin, so every request is same-origin and relative.
# Set explicitly rather than left unset so a stray value in the environment
# cannot reach the bundle.
ENV NEXT_PUBLIC_API_BASE=""
ENV NEXT_TELEMETRY_DISABLED=1

RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2 — the runtime image
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# uv by copy from its own published image: no curl, no install script, no
# network fetch inside the build beyond the layers Docker already caches.
# Pinned, because "latest" is not a build you can reproduce next month.
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependencies before source, for the same reason as the npm layer above.
# --no-install-project: the application is copied in as plain files below and
# imported from the working directory, so there is nothing to build a wheel
# for — and rebuilding one on every source edit would defeat this layer.
# README.md is here only because pyproject.toml's `readme` names it; uv reads
# the metadata even when it does not install the project.
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/app ./app
COPY --from=frontend /build/out ./static

# PLAN.md §5: both are read at call time and both have working defaults, but
# the defaults are derived from a *source checkout* (app/paths.py) which does
# not exist here. Set them explicitly rather than relying on a fallback search.
ENV DB_PATH=/app/db/finally.db \
    STATIC_DIR=/app/static \
    LOG_LEVEL=INFO \
    PATH="/app/.venv/bin:$PATH"

# Non-root. The database directory is created and given to the user *in the
# image*, which is what makes a fresh named volume mounted over it inherit that
# ownership — Docker copies the image directory's contents and mode into a new
# named volume. Without this the volume arrives owned by root and the first
# write fails with "unable to open database file".
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin finally \
    && mkdir -p /app/db \
    && chown -R finally:finally /app
USER finally

EXPOSE 8000

# No curl in the image, so the probe is the interpreter that is already here.
# start-period covers the first-request database creation and the market feed
# coming up.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/health', timeout=4).read()"]

# Exec form, so uvicorn is PID 1 and receives the SIGTERM `docker stop` sends —
# which is what runs the lifespan's shutdown and stops the market source
# cleanly instead of the container being killed ten seconds later.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
