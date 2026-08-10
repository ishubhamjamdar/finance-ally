#!/usr/bin/env bash
# Gate 3, step 1: every backend exit criterion, against a real running server.
#
# PLAN.md requires exit criteria to be verified by running them, not by reading
# the code. Checkpoint 3 did that by hand-assembling curl at the terminal, twice
# — once before the review and once after it changed the code. This script makes
# the second run free, which is the whole point of putting verification after
# review rather than before it.
#
#   test/smoke.sh              # starts its own server on a throwaway database
#   test/smoke.sh 8000         # checks a server already running on that port
#
# Exits non-zero on the first failed check.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${1:-8137}"
BASE="http://localhost:${PORT}/api"
OWN_SERVER=0
FAILURES=0
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"; [ "$OWN_SERVER" = 1 ] && kill "$SERVER_PID" 2>/dev/null; exit' EXIT

check() { # check <label> <expected> <actual>
    if [ "$2" = "$3" ]; then
        printf '  ok    %-52s %s\n' "$1" "$3"
    else
        printf '  FAIL  %-52s expected %s, got %s\n' "$1" "$2" "$3"
        FAILURES=$((FAILURES + 1))
    fi
}

code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
post() { curl -s -o /dev/null -w '%{http_code}' -X POST -H 'content-type: application/json' -d "$2" "$1"; }

if ! curl -sf "${BASE}/health" >/dev/null 2>&1; then
    echo "Starting a server on port ${PORT} with a throwaway database..."
    DB_PATH="${TMP}/finally.db" MASSIVE_API_KEY= \
        uv run --directory "${REPO}/backend" uvicorn app.main:app --port "${PORT}" \
        >"${TMP}/server.log" 2>&1 &
    SERVER_PID=$!
    OWN_SERVER=1
    for _ in $(seq 1 30); do
        curl -sf "${BASE}/health" >/dev/null 2>&1 && break
        sleep 0.5
    done
fi

echo "System"
check "GET /api/health" 200 "$(code "${BASE}/health")"

echo "Watchlist"
check "POST /api/watchlist (new)" 201 "$(post "${BASE}/watchlist" '{"ticker":"pypl"}')"
check "POST /api/watchlist (duplicate)" 409 "$(post "${BASE}/watchlist" '{"ticker":"PYPL"}')"
check "POST /api/watchlist (not a symbol)" 422 "$(post "${BASE}/watchlist" '{"ticker":"../../etc"}')"
check "DELETE /api/watchlist (not watched)" 404 "$(code -X DELETE "${BASE}/watchlist/ZZZZ")"

echo "Trading"
check "POST /trade (buy)" 201 "$(post "${BASE}/portfolio/trade" '{"ticker":"AAPL","side":"buy","quantity":5}')"
check "POST /trade (insufficient cash)" 400 "$(post "${BASE}/portfolio/trade" '{"ticker":"AAPL","side":"buy","quantity":9999}')"
check "POST /trade (oversell)" 400 "$(post "${BASE}/portfolio/trade" '{"ticker":"AAPL","side":"sell","quantity":9999}')"
check "POST /trade (zero quantity)" 422 "$(post "${BASE}/portfolio/trade" '{"ticker":"AAPL","side":"buy","quantity":0}')"
check "POST /trade (client-supplied price)" 422 "$(post "${BASE}/portfolio/trade" '{"ticker":"AAPL","side":"buy","quantity":1,"price":1}')"
check "GET /portfolio" 200 "$(code "${BASE}/portfolio")"
check "GET /history?limit=0" 422 "$(code "${BASE}/portfolio/history?limit=0")"

echo "Exit criteria"
# A held ticker keeps streaming after it leaves the watchlist: the tracked set
# is watchlist union positions, and dropping its price would make the portfolio
# total silently lose that position.
check "DELETE a held ticker reports still_tracked" true \
    "$(curl -s -X DELETE "${BASE}/watchlist/aapl" | python3 -c 'import json,sys; print(str(json.load(sys.stdin)["still_tracked"]).lower())')"
check "the position survives it" 1 \
    "$(curl -s "${BASE}/portfolio" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["positions"]))')"
check "a trade wrote a snapshot immediately" true \
    "$(curl -s "${BASE}/portfolio/history" | python3 -c 'import json,sys; print(str(len(json.load(sys.stdin)["snapshots"]) > 0).lower())')"
# A ticker added at runtime must reach the stream without a restart, and a
# held-but-unwatched one must still be in it.
check "runtime-added + held tickers both stream" "True True" \
    "$(curl -sN --max-time 5 "${BASE}/stream/prices" | grep -m1 '^data: ' | python3 -c 'import json,sys; p=json.loads(sys.stdin.read().split("data: ",1)[1]); print("PYPL" in p, "AAPL" in p)')"

echo
if [ "$FAILURES" -eq 0 ]; then
    echo "All checks passed."
else
    echo "${FAILURES} check(s) failed."
fi
exit "$FAILURES"
