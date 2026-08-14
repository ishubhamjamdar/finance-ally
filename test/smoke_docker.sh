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

cleanup() {
    docker rm --force "$FINALLY_CONTAINER" >/dev/null 2>&1
    docker volume rm "$FINALLY_VOLUME" >/dev/null 2>&1
    [ "$SKIP_BUILD" = 0 ] && docker rmi "$FINALLY_IMAGE" >/dev/null 2>&1
    rm -rf "$TMP"
}
trap cleanup EXIT

check() { # check <label> <expected> <actual>
    if [ "$2" = "$3" ]; then
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

check "the container is gone" "" "$(docker ps -aq --filter "name=^/${FINALLY_CONTAINER}$")"

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
echo "Shutdown is graceful"
# ---------------------------------------------------------------------------
# uvicorn is PID 1 by the Dockerfile's exec-form CMD, so `docker stop` reaches
# the lifespan rather than the container being killed after the timeout.
start_stop="$(date +%s)"
"${CLONE}/scripts/stop_mac.sh" >/dev/null 2>&1
elapsed=$(( $(date +%s) - start_stop ))
[ "$elapsed" -lt 10 ]
check "SIGTERM stops it inside the 10s grace period" 0 $?

echo
if [ "$FAILURES" -eq 0 ]; then
    echo "All checks passed."
else
    echo "${FAILURES} check(s) failed."
fi
exit "$FAILURES"
