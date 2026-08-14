#!/usr/bin/env bash
# Launch FinAlly (macOS / Linux). PLAN.md §11.
#
#   scripts/start_mac.sh              # build if needed, run, open the browser
#   scripts/start_mac.sh --build      # force a rebuild, recreate the container
#   scripts/start_mac.sh --no-open    # don't touch the browser (CI, smoke tests)
#   FINALLY_PORT=8010 scripts/start_mac.sh
#
# Idempotent: running it twice does nothing the second time but print the URL.
# The container port is always 8000; FINALLY_PORT only changes the host side.

set -euo pipefail

# The defaults are the deployment: docker-compose.yml names the same image,
# container, volume and port, so the two front doors share one database. The
# overrides exist so `test/smoke_docker.sh` can exercise this script end to end
# without stopping the instance you are actually using.
IMAGE="${FINALLY_IMAGE:-finally:latest}"
CONTAINER="${FINALLY_CONTAINER:-finally}"
VOLUME="${FINALLY_VOLUME:-finally-data}"
PORT="${FINALLY_PORT:-8000}"
# Loopback, not 0.0.0.0. `docker run -p 8000:8000` publishes on every interface,
# and FinAlly has no login by design (PLAN.md §2) — so on a shared network that
# would hand anyone the portfolio, the watchlist, and a POST /api/chat that
# spends *your* OpenRouter credits. This is a localhost app; bind it there.
# FINALLY_BIND=0.0.0.0 for the deliberate case of reaching it from elsewhere.
BIND="${FINALLY_BIND:-127.0.0.1}"
# How long `docker stop` waits for SIGTERM to be honoured before SIGKILL. The
# container declares it, because the host default is not what it is usually
# said to be: measured on Docker 29 here, it is 1.1 seconds, and the app asks
# uvicorn for up to 3 to close an open price stream. Without this the lifespan
# is killed mid-shutdown — the snapshot task never awaited, the source never
# stopped — which is exactly the state the comments in stop_mac.sh describe as
# impossible.
STOP_TIMEOUT=15
# Absolute, and derived from this file rather than the caller's cwd: the build
# context is the repo root and `docker build .` from anywhere else builds the
# wrong thing.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

FORCE_BUILD=0
OPEN_BROWSER=1
for arg in "$@"; do
    case "$arg" in
        --build) FORCE_BUILD=1 ;;
        --no-open) OPEN_BROWSER=0 ;;
        # The header down to its first blank line — a fixed line count drifts
        # the moment a line is added, and printed `set -euo pipefail` as help.
        -h|--help) sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is not installed. Install Docker Desktop: https://docker.com/get-started" >&2
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    echo "Docker is installed but the daemon is not running. Start Docker Desktop and try again." >&2
    exit 1
fi

# Absent .env is a working configuration — simulator prices, no AI chat — so
# this is a warning, not a failure. PLAN.md §5: every other variable defaults.
if [ ! -f "${REPO}/.env" ]; then
    echo "Note: no .env found at ${REPO}/.env."
    echo "      The app will run on simulated market data; the AI chat needs OPENROUTER_API_KEY."
    echo "      cp .env.example .env and add your key to enable it."
fi

running_id="$(docker ps --quiet --filter "name=^/${CONTAINER}$")"
if [ -n "$running_id" ] && [ "$FORCE_BUILD" = 0 ]; then
    # Already up. Report the port it is actually published on rather than the
    # one we would have chosen — they differ if FINALLY_PORT changed since.
    actual="$(docker port "$CONTAINER" 8000/tcp 2>/dev/null | head -1 | sed 's/.*://')"
    echo "FinAlly is already running at http://localhost:${actual:-$PORT}"
    exit 0
fi

if [ "$FORCE_BUILD" = 1 ] || [ -z "$(docker images --quiet "$IMAGE")" ]; then
    echo "Building ${IMAGE}..."
    docker build -t "$IMAGE" "$REPO"
fi

existing_id="$(docker ps --all --quiet --filter "name=^/${CONTAINER}$")"
if [ -n "$existing_id" ]; then
    if [ "$FORCE_BUILD" = 1 ]; then
        # A rebuilt image is only reached by recreating the container. The
        # volume is untouched, so the portfolio survives.
        echo "Recreating ${CONTAINER} on the new image..."
        docker rm --force "$CONTAINER" >/dev/null
    else
        echo "Starting the existing ${CONTAINER} container..."
        docker start "$CONTAINER" >/dev/null
    fi
fi

if [ -z "$(docker ps --quiet --filter "name=^/${CONTAINER}$")" ]; then
    # Only a listener that is not ours is a conflict; our own container was
    # handled above.
    #
    # `lsof` is on every macOS but not on every Linux this script claims to
    # support, and `if lsof ...` reads a missing command (127) as "the port is
    # free" — so the guard would vanish silently on exactly the hosts that need
    # it, leaving a raw docker error instead of these two lines. Try the other
    # probe, and say so when neither is available.
    port_in_use=""
    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 && port_in_use=1
    elif command -v ss >/dev/null 2>&1; then
        ss -ltn "sport = :${PORT}" 2>/dev/null | grep -q LISTEN && port_in_use=1
    else
        echo "Note: no lsof or ss here, so port ${PORT} was not checked first."
    fi

    if [ -n "$port_in_use" ]; then
        echo "Port ${PORT} is already in use by another process." >&2
        echo "Free it, or choose another: FINALLY_PORT=8010 scripts/start_mac.sh" >&2
        exit 1
    fi

    env_args=()
    [ -f "${REPO}/.env" ] && env_args=(--env-file "${REPO}/.env")

    echo "Starting ${CONTAINER} on port ${PORT}..."
    docker run --detach \
        --name "$CONTAINER" \
        --publish "${BIND}:${PORT}:8000" \
        --volume "${VOLUME}:/app/db" \
        --restart unless-stopped \
        --stop-timeout "$STOP_TIMEOUT" \
        "${env_args[@]}" \
        "$IMAGE" >/dev/null
fi

# A container that already existed keeps the mapping it was created with, and
# `docker start` cannot change it. Ask it which port it actually publishes,
# rather than polling the one we would have chosen and reporting a healthy app
# as a 60-second timeout.
published="$(docker port "$CONTAINER" 8000/tcp 2>/dev/null | head -1 | sed 's/.*://')"
if [ -n "$published" ] && [ "$published" != "$PORT" ]; then
    echo "Note: the existing container publishes ${published}, not ${PORT}."
    echo "      To move it: scripts/stop_mac.sh, then start again."
    PORT="$published"
fi

URL="http://localhost:${PORT}"
printf 'Waiting for FinAlly to come up'
for _ in $(seq 1 60); do
    if curl -sf --max-time 2 "${URL}/api/health" >/dev/null 2>&1; then
        echo
        echo "FinAlly is running at ${URL}"
        if [ "$OPEN_BROWSER" = 1 ]; then
            if command -v open >/dev/null 2>&1; then open "$URL"
            elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 || true
            fi
        fi
        echo "Stop it with scripts/stop_mac.sh"
        exit 0
    fi
    printf '.'
    sleep 1
done

echo
echo "FinAlly did not become healthy within 60s. Recent logs:" >&2
docker logs --tail 30 "$CONTAINER" >&2 || true
exit 1
