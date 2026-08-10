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
    # LLM_MOCK=true so the chat checks below need no key and no network. The
    # live call is a separate, opt-in check at the end.
    DB_PATH="${TMP}/finally.db" MASSIVE_API_KEY= LLM_MOCK=true \
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

echo "Chat (LLM_MOCK, so no key and no network)"
check "POST /api/chat" 200 "$(post "${BASE}/chat" '{"message":"how am I doing?"}')"
check "POST /api/chat (empty message)" 422 "$(post "${BASE}/chat" '{"message":"   "}')"
check "POST /api/chat (unexpected field)" 422 "$(post "${BASE}/chat" '{"message":"hi","user_id":"other"}')"
check "GET /api/chat/history" 200 "$(code "${BASE}/chat/history")"
check "GET /api/chat/history?limit=0" 422 "$(code "${BASE}/chat/history?limit=0")"

# Exit criterion: a mocked trade actually moves cash and positions.
CASH_BEFORE="$(curl -s "${BASE}/portfolio" | python3 -c 'import json,sys; print(json.load(sys.stdin)["cash_balance"])')"
curl -s -o "${TMP}/chat.json" -X POST -H 'content-type: application/json' \
    -d '{"message":"buy 2 MSFT"}' "${BASE}/chat"
check "a chat trade executed" true \
    "$(python3 -c 'import json; d=json.load(open("'"${TMP}"'/chat.json")); print(str(any(a["ok"] and a["kind"]=="trade" for a in d["actions"])).lower())')"
check "a chat trade moved cash" true \
    "$(curl -s "${BASE}/portfolio" | python3 -c 'import json,sys; print(str(json.load(sys.stdin)["cash_balance"] < '"${CASH_BEFORE}"').lower())')"

# Exit criterion: a trade that fails validation returns its error rather than
# vanishing — and does not become a non-200.
curl -s -o "${TMP}/refused.json" -w '%{http_code}' -X POST -H 'content-type: application/json' \
    -d '{"message":"buy 100000 AAPL"}' "${BASE}/chat" >"${TMP}/refused.code"
check "a refused chat trade is still a 200" 200 "$(cat "${TMP}/refused.code")"
check "and reports why it was refused" true \
    "$(python3 -c 'import json; d=json.load(open("'"${TMP}"'/refused.json")); print(str(any(not a["ok"] and "Insufficient cash" in a["detail"] for a in d["actions"])).lower())')"

# Exit criterion: messages and their actions persist, and replay as history.
check "the exchange persisted with its actions" true \
    "$(curl -s "${BASE}/chat/history" | python3 -c '
import json, sys
messages = json.load(sys.stdin)["messages"]
roles = [m["role"] for m in messages]
executed = any(m["actions"] for m in messages if m["role"] == "assistant")
print(str(roles[:2] == ["user", "assistant"] and executed).lower())')"

echo "Chat (live OpenRouter — set LIVE_LLM=1 to include)"
if [ "${LIVE_LLM:-0}" = "1" ]; then
    # Exit criterion: one live call succeeds, confirming the model id, the
    # Cerebras provider routing and structured-output handling. Deliberately
    # opt-in: it costs money and needs a key, so the default run stays free.
    check "a live call returns a usable reply" true \
        "$(uv run --directory "${REPO}/backend" python -c '
from app.config import load_env
from app.llm import complete, parse_reply
load_env()
reply = parse_reply(complete([
    {"role": "system", "content": "Reply as JSON with message, trades, watchlist_changes."},
    {"role": "user", "content": "In one sentence, what is a market order?"},
]))
print(str(bool(reply.message) and not reply.rejected).lower())' 2>/dev/null || echo "false")"
else
    echo "  skip  live OpenRouter call (LIVE_LLM unset)"
fi

echo
if [ "$FAILURES" -eq 0 ]; then
    echo "All checks passed."
else
    echo "${FAILURES} check(s) failed."
fi
exit "$FAILURES"
