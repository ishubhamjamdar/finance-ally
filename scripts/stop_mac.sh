#!/usr/bin/env bash
# Stop FinAlly (macOS / Linux). PLAN.md §11.
#
#   scripts/stop_mac.sh
#
# Stops and removes the container. **Never removes the volume** — the portfolio,
# the watchlist and the chat history live in `finally-data`, and the whole point
# of the named volume is that start/stop/start leaves them intact. Removing it
# is a deliberate act:
#
#   docker volume rm finally-data
#
# Idempotent: stopping something that is not running is a success, not an error.

set -euo pipefail

# Overridable for the same reason as in start_mac.sh: the smoke script stops
# its own container, never yours.
CONTAINER="${FINALLY_CONTAINER:-finally}"

if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is not installed — nothing to stop."
    exit 0
fi

if ! docker info >/dev/null 2>&1; then
    echo "The Docker daemon is not running — nothing to stop."
    exit 0
fi

if [ -z "$(docker ps --all --quiet --filter "name=^/${CONTAINER}$")" ]; then
    echo "FinAlly is not running."
    exit 0
fi

# `docker stop` sends SIGTERM first, which uvicorn — PID 1 by the Dockerfile's
# exec-form CMD — turns into the lifespan shutdown: the snapshot task is
# cancelled and awaited, and the market source is stopped.
echo "Stopping ${CONTAINER}..."
docker stop "$CONTAINER" >/dev/null
docker rm "$CONTAINER" >/dev/null

echo "Stopped. Your portfolio is preserved in the 'finally-data' volume."
echo "Start it again with scripts/start_mac.sh"
