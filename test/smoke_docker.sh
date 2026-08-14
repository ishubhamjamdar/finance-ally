#!/usr/bin/env bash
# Gate 3, step 1 for Checkpoint 8: every packaging exit criterion, run for real.
#
# PLAN.md requires exit criteria to be verified by running them, not by reading
# the code, and requires the commands to live in a re-runnable script so the
# second run costs nothing. This is that script for the container.
#
#   test/smoke_docker.sh            # full run: clean clone, build, start, stop
#   test/smoke_docker.sh --no-build # reuse the image from a previous run
#
# It never touches your own deployment. The clone, the image, the container,
# the volume and the port are all smoke-only names, and the volume and image
# are removed at the end.
#
# Exits non-zero on the first failed check.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export FINALLY_IMAGE="finally:smoke"
export FINALLY_CONTAINER="finally-smoke"
export FINALLY_VOLUME="finally-smoke-data"
export FINALLY_PORT="${FINALLY_PORT:-8137}"

BASE="http://localhost:${FINALLY_PORT}"
API="${BASE}/api"
FAILURES=0
SKIP_BUILD=0
[ "${1:-}" = "--no-build" ] && SKIP_BUILD=1

TMP="$(mktemp -d)"
CLONE="${TMP}/clone"

# The container and the smoke volume go; the image stays. Removing it made
# `--no-build` impossible — every normal run deleted the image the flag exists
# to reuse, and start_mac.sh silently rebuilt it from the clone, which is
# minutes of the thing that was meant to be skipped, reported as success.
cleanup() {
    docker rm --force "$FINALLY_CONTAINER" "${FINALLY_CONTAINER}-sse" >/dev/null 2>&1
    docker volume rm "$FINALLY_VOLUME" >/dev/null 2>&1
    rm -rf "$TMP"
}
trap cleanup EXIT

check() { # check <label> <expected> <actual>
    # An empty "actual" is a failure even when the expectation is also empty.
    # Without this, "cash survived the restart" passed against a server that
    # never started: both sides were the empty string, and the one check that
    # proves the volume works reported ok inside a run of nineteen failures.
    if [ -z "$3" ] && [ -z "$2" ]; then
        printf '  FAIL  %-56s both sides empty — nothing was measured\n' "$1"
        FAILURES=$((FAILURES + 1))
    elif [ "$2" = "$3" ]; then
        printf '  ok    %-56s %s\n' "$1" "$3"
    else
        printf '  FAIL  %-56s expected %s, got %s\n' "$1" "$2" "$3"
        FAILURES=$((FAILURES + 1))
    fi
}

contains() { # contains <label> <needle> <haystack>
    case "$3" in
        *"$2"*) printf '  ok    %-56s contains %s\n' "$1" "$2" ;;
        *)
            printf '  FAIL  %-56s expected to contain %s\n' "$1" "$2"
            printf '        got: %.200s\n' "$3"
            FAILURES=$((FAILURES + 1))
            ;;
    esac
}

code() { curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$@"; }
cash() { curl -s --max-time 10 "${API}/portfolio" | sed -n 's/.*"cash_balance":\([0-9.]*\).*/\1/p'; }

if ! docker info >/dev/null 2>&1; then
    echo "Docker is not running — nothing to smoke." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
echo "Exit criterion 1: docker build from a clean clone"
# ---------------------------------------------------------------------------
# A real clone of the committed branch, not a copy of the working tree. This is
# the check Checkpoint 5 did not have: six files existed on disk, none of them
# were committed, and every local command passed against the untracked copies.
BRANCH="$(git -C "$REPO" rev-parse --abbrev-ref HEAD)"
git clone --quiet --local --no-hardlinks --single-branch --branch "$BRANCH" "$REPO" "$CLONE" 2>/dev/null
check "the branch clones" 0 $?

for required in Dockerfile .dockerignore docker-compose.yml .env.example \
    scripts/start_mac.sh scripts/stop_mac.sh scripts/start_windows.ps1 scripts/stop_windows.ps1; do
    [ -e "${CLONE}/${required}" ]
    check "the clone has ${required}" 0 $?
done

# No toolchain came with it: the build must install everything it needs.
for absent in frontend/node_modules frontend/out backend/.venv .env; do
    [ ! -e "${CLONE}/${absent}" ]
    check "the clone has no ${absent}" 0 $?
done

if [ "$SKIP_BUILD" = 0 ]; then
    echo "  building (a few minutes on a cold cache)..."
    docker build -t "$FINALLY_IMAGE" "$CLONE" >"${TMP}/build.log" 2>&1
    check "docker build ." 0 $?
    if [ "$FAILURES" -gt 0 ]; then
        tail -30 "${TMP}/build.log"
        exit 1
    fi
fi

SIZE_MB="$(docker image inspect "$FINALLY_IMAGE" --format '{{.Size}}' | awk '{printf "%.0f", $1/1048576}')"
printf '  info  %-56s %s MB\n' "image size" "$SIZE_MB"

# ---------------------------------------------------------------------------
echo "Secrets and permissions (PLAN.md §13, Checkpoint 8's review focus)"
# ---------------------------------------------------------------------------
env_in_image="$(docker run --rm --entrypoint sh "$FINALLY_IMAGE" -c 'ls -a /app | grep -c "^\.env$"' 2>/dev/null)"
check "no .env anywhere in /app" 0 "${env_in_image:-0}"

history_hits="$(docker history --no-trunc "$FINALLY_IMAGE" | grep -ci 'OPENROUTER_API_KEY=[^ ]' )"
check "no API key in the image history" 0 "$history_hits"

whoami_out="$(docker run --rm --entrypoint id "$FINALLY_IMAGE" -un 2>/dev/null)"
check "the image runs as a non-root user" "finally" "$whoami_out"

# ---------------------------------------------------------------------------
echo "Exit criterion 4a: start_mac.sh, twice in a row"
# ---------------------------------------------------------------------------
# Run from the clone, so this also exercises the scripts as a clean clone ships
# them — and with no .env present, which is exit criterion 5's second half.
"${CLONE}/scripts/start_mac.sh" --no-open >"${TMP}/start1.log" 2>&1
check "first start exits 0" 0 $?
contains "first start reports the URL" "$BASE" "$(cat "${TMP}/start1.log")"
contains "no .env is a warning, not an error" "no .env found" "$(cat "${TMP}/start1.log")"

"${CLONE}/scripts/start_mac.sh" --no-open >"${TMP}/start2.log" 2>&1
check "second start exits 0" 0 $?
contains "second start is a no-op" "already running" "$(cat "${TMP}/start2.log")"

# ---------------------------------------------------------------------------
echo "Exit criterion 2: the container serves the UI and the API"
# ---------------------------------------------------------------------------
# The criterion names port 8000, which is the *container* port; the host side is
# a mapping, and this script uses a spare one so it cannot collide with whatever
# is already on 8000.
mapped="$(docker port "$FINALLY_CONTAINER" 8000/tcp | head -1)"
contains "the container listens on 8000" ":${FINALLY_PORT}" "$mapped"
# The security review's finding: an app with no login must not be published on
# every interface. The binding, not just the port, is part of the contract.
contains "published on loopback only" "127.0.0.1:${FINALLY_PORT}" "$mapped"

check "GET /api/health" 200 "$(code "${API}/health")"
contains "the feed is running" '"database":"ok"' "$(curl -s "${API}/health")"
check "GET /api/portfolio" 200 "$(code "${API}/portfolio")"
check "GET /api/watchlist" 200 "$(code "${API}/watchlist")"
check "GET / (the UI)" 200 "$(code "$BASE")"
contains "the UI is the exported app" "FinAlly" "$(curl -s "$BASE" | head -c 2000)"
contains "the bundle is same-origin" 'src="/_next/' "$(curl -s "$BASE" | head -c 4000)"

frames="$(curl -s --max-time 4 -N "${API}/stream/prices" | grep -c '^data:')"
[ "${frames:-0}" -gt 0 ]
check "SSE frames arrive" 0 $?

# ---------------------------------------------------------------------------
echo "Exit criterion 3: a trade survives stop and start"
# ---------------------------------------------------------------------------
before="$(cash)"
check "POST /api/portfolio/trade" 201 \
    "$(code -X POST -H 'content-type: application/json' \
        -d '{"ticker":"AAPL","quantity":5,"side":"buy"}' "${API}/portfolio/trade")"
after_trade="$(cash)"
[ -n "$after_trade" ] && [ "$after_trade" != "$before" ]
check "the trade moved cash" 0 $?

"${CLONE}/scripts/stop_mac.sh" >"${TMP}/stop1.log" 2>&1
check "stop exits 0" 0 $?
contains "stop keeps the volume" "preserved" "$(cat "${TMP}/stop1.log")"

# Exit criterion 4b: stopping what is already stopped.
"${CLONE}/scripts/stop_mac.sh" >"${TMP}/stop2.log" 2>&1
check "second stop exits 0" 0 $?
contains "second stop is a no-op" "not running" "$(cat "${TMP}/stop2.log")"

# Counted, not compared to the empty string: `check` now treats two empty
# sides as nothing measured, and a count says the same thing without relying on
# emptiness to mean success.
check "the container is gone" 0 \
    "$(docker ps -aq --filter "name=^/${FINALLY_CONTAINER}$" | wc -l | tr -d ' ')"

"${CLONE}/scripts/start_mac.sh" --no-open >"${TMP}/start3.log" 2>&1
check "restart exits 0" 0 $?
check "cash survived the restart" "$after_trade" "$(cash)"
contains "the position survived the restart" '"ticker":"AAPL"' "$(curl -s "${API}/portfolio")"

# ---------------------------------------------------------------------------
echo "Exit criterion 5: an empty MASSIVE_API_KEY and a one-line .env"
# ---------------------------------------------------------------------------
# The runs above had no .env at all. This one has the minimum the README asks
# for and nothing else, which is the documented starting point.
"${CLONE}/scripts/stop_mac.sh" >/dev/null 2>&1
printf 'OPENROUTER_API_KEY=sk-smoke-not-a-real-key\n' >"${CLONE}/.env"
"${CLONE}/scripts/start_mac.sh" --no-open >"${TMP}/start4.log" 2>&1
check "starts with a one-line .env" 0 $?
health="$(curl -s "${API}/health")"
contains "the simulator is the source" "SimulatorDataSource" "$health"
check "trading still works" 201 \
    "$(code -X POST -H 'content-type: application/json' \
        -d '{"ticker":"MSFT","quantity":1,"side":"buy"}' "${API}/portfolio/trade")"

# MASSIVE_API_KEY explicitly empty rather than merely absent — the two reach
# different branches of the factory.
"${CLONE}/scripts/stop_mac.sh" >/dev/null 2>&1
printf 'OPENROUTER_API_KEY=sk-smoke-not-a-real-key\nMASSIVE_API_KEY=\n' >"${CLONE}/.env"
"${CLONE}/scripts/start_mac.sh" --no-open >"${TMP}/start5.log" 2>&1
check "starts with MASSIVE_API_KEY empty" 0 $?
contains "still the simulator" "SimulatorDataSource" "$(curl -s "${API}/health")"

# ---------------------------------------------------------------------------
echo "Shutdown is graceful with a price stream open"
# ---------------------------------------------------------------------------
# uvicorn is PID 1 by the Dockerfile's exec-form CMD, so `docker stop` reaches
# the lifespan rather than the container being killed after the grace period.
#
# **With an SSE client attached**, which is what the browser does and what the
# first version of this check missed by testing an idle container. `/api/stream
# /prices` is a response that never ends, and uvicorn's default is to wait for
# in-flight responses forever: the container logged "Waiting for connections to
# close" and died of SIGKILL with the lifespan never run. The container is
# driven directly here rather than through stop_mac.sh, because the script
# removes the container and the exit code is the evidence.
"${CLONE}/scripts/stop_mac.sh" >/dev/null 2>&1
SSE="${FINALLY_CONTAINER}-sse"
docker rm --force "$SSE" >/dev/null 2>&1
# --stop-timeout as start_mac.sh passes it: the measured host default is ~1s,
# which SIGKILLs the shutdown this section is checking.
docker run --detach --name "$SSE" --publish "127.0.0.1:${FINALLY_PORT}:8000" \
    --stop-timeout 15 "$FINALLY_IMAGE" >/dev/null
for _ in $(seq 1 30); do
    curl -sf --max-time 2 "${API}/health" >/dev/null 2>&1 && break
    sleep 1
done

curl -s -N --max-time 30 "${API}/stream/prices" >/dev/null 2>&1 &
STREAM_PID=$!
sleep 3

started="$(date +%s)"
docker stop "$SSE" >/dev/null
elapsed=$(( $(date +%s) - started ))
kill "$STREAM_PID" 2>/dev/null

check "the container exits cleanly, not by SIGKILL (137)" 0 \
    "$(docker inspect --format '{{.State.ExitCode}}' "$SSE")"
contains "the lifespan ran to completion" "Application shutdown complete" \
    "$(docker logs "$SSE" 2>&1 | tail -5)"
[ "$elapsed" -lt 10 ]
check "stopped inside Docker's 10s grace period" 0 $?
docker rm --force "$SSE" >/dev/null 2>&1

echo
echo "The ${FINALLY_IMAGE} image is kept for a --no-build re-run."
echo "Remove it with: docker rmi ${FINALLY_IMAGE}"
echo
if [ "$FAILURES" -eq 0 ]; then
    echo "All checks passed."
else
    echo "${FAILURES} check(s) failed."
fi
exit "$FAILURES"
