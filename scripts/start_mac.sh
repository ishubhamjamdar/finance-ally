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
        -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
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
    if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "Port ${PORT} is already in use by another process." >&2
        echo "Free it, or choose another: FINALLY_PORT=8010 scripts/start_mac.sh" >&2
        exit 1
    fi

    env_args=()
    [ -f "${REPO}/.env" ] && env_args=(--env-file "${REPO}/.env")

    echo "Starting ${CONTAINER} on port ${PORT}..."
    docker run --detach \
        --name "$CONTAINER" \
        --publish "${PORT}:8000" \
        --volume "${VOLUME}:/app/db" \
        --restart unless-stopped \
        "${env_args[@]}" \
        "$IMAGE" >/dev/null
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
