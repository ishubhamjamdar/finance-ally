#!/usr/bin/env bash
# Gate 3 for Checkpoint 5: the frontend's exit criteria, against a real build
# and a real server.
#
# The sibling `smoke.sh` does this for the API. This one covers what the export
# has to do: build clean, be served by FastAPI on the same port as the API, and
# carry a page that opens the price stream.
#
# The two criteria this script cannot reach — the flash fading, and the dot
# going amber then red when the backend dies — are browser behaviours, verified
# separately by driving a real browser (see the Checkpoint 5 log entry). Every
# checkable thing is here so the second run costs nothing.
#
# Checkpoint 6 added the second half: the panels are in the export, and the
# endpoints behind them answer with the shapes and the status codes the UI
# renders differently — a 201 fill, a 400 the account could not support, a 422
# that was malformed, and a snapshot written at the trade's own timestamp.
#
#   test/smoke_frontend.sh              # builds, starts its own server
#   test/smoke_frontend.sh --no-build   # reuse an existing frontend/out
#
# Exits non-zero on the first failed check.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND="${REPO}/frontend"
PORT="${PORT:-8139}"
BASE="http://localhost:${PORT}"
BUILD=1
[ "${1:-}" = "--no-build" ] && BUILD=0

FAILURES=0
SERVER_PID=""
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"; [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null; exit' EXIT

check() { # check <label> <expected> <actual>
    if [ "$2" = "$3" ]; then
        printf '  ok    %-52s %s\n' "$1" "$3"
    else
        printf '  FAIL  %-52s expected %s, got %s\n' "$1" "$2" "$3"
        FAILURES=$((FAILURES + 1))
    fi
}

contains() { # contains <label> <needle> <file>
    if grep -qF "$2" "$3"; then
        printf '  ok    %-52s %s\n' "$1" "found"
    else
        printf '  FAIL  %-52s %s not in %s\n' "$1" "$2" "$3"
        FAILURES=$((FAILURES + 1))
    fi
}

code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

echo "Build"
if [ "$BUILD" = 1 ]; then
    [ -d "${FRONTEND}/node_modules" ] || (cd "${FRONTEND}" && npm install >/dev/null 2>&1)
    if (cd "${FRONTEND}" && npm run build >"${TMP}/build.log" 2>&1); then
        check "npm run build" 0 0
    else
        check "npm run build" 0 1
        tail -20 "${TMP}/build.log"
    fi
fi
check "out/index.html exists" true "$([ -f "${FRONTEND}/out/index.html" ] && echo true || echo false)"
check "the export is static — no server bundle" true \
    "$([ ! -d "${FRONTEND}/out/_next/server" ] && echo true || echo false)"

echo "Every source file is visible to git"
# The check that was missing. The root .gitignore's unanchored `lib/` — from
# GitHub's Python template — silently excluded `frontend/src/lib/`: six files,
# none committed, a clean clone that could not build, and every local check
# passing because the files were sitting untracked on disk.
check "nothing under frontend/src is ignored" 0 \
    "$(git -C "${REPO}" ls-files --others --ignored --exclude-standard -- frontend/src | wc -l | tr -d ' ')"
check "nothing under frontend/src is untracked" 0 \
    "$(git -C "${REPO}" ls-files --others --exclude-standard -- frontend/src | wc -l | tr -d ' ')"

echo "Quality gates"
(cd "${FRONTEND}" && npm run lint >"${TMP}/lint.log" 2>&1)
check "npm run lint" 0 $?
(cd "${FRONTEND}" && npm run typecheck >"${TMP}/tsc.log" 2>&1)
check "tsc --noEmit" 0 $?
(cd "${FRONTEND}" && npm test >"${TMP}/test.log" 2>&1)
check "npm test" 0 $?

echo "Served by the backend"
# STATIC_DIR is set explicitly rather than left to the search path, so this
# checks the export that was just built and not whatever else is on disk.
# LLM_MOCK, so the chat checks below are deterministic and free and need no
# API key — PLAN.md §9's stated purpose for it.
DB_PATH="${TMP}/finally.db" MASSIVE_API_KEY= LLM_MOCK=true STATIC_DIR="${FRONTEND}/out" \
    uv run --directory "${REPO}/backend" uvicorn app.main:app --port "${PORT}" \
    >"${TMP}/server.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 40); do
    curl -sf "${BASE}/api/health" >/dev/null 2>&1 && break
    sleep 0.5
done

check "GET /api/health" 200 "$(code "${BASE}/api/health")"
check "GET / serves the UI" 200 "$(code "${BASE}/")"
curl -s "${BASE}/" >"${TMP}/index.html"
contains "the page is FinAlly" "FinAlly" "${TMP}/index.html"
contains "it loads its bundle" "/_next/static" "${TMP}/index.html"
contains "the backend logged the static mount" "Serving frontend from" "${TMP}/server.log"

echo "Live prices on the same origin"
# The page opens exactly this URL. Two frames prove it is streaming rather than
# answering once; the tickers prove the seeded watchlist is behind it.
# `--max-time`, not `timeout(1)`: macOS ships no coreutils, and `timeout` there
# is not on PATH. curl exits 28 when it cuts the stream off, which is expected.
curl -sN --max-time 4 "${BASE}/api/stream/prices" >"${TMP}/stream.txt" 2>/dev/null
FRAMES="$(grep -c '^data: ' "${TMP}/stream.txt")"
check "at least two price frames in 4s" true "$([ "${FRAMES:-0}" -ge 2 ] && echo true || echo false)"
contains "frames carry the seeded tickers" '"AAPL"' "${TMP}/stream.txt"
contains "EventSource is told when to retry" "retry:" "${TMP}/stream.txt"
check "a watchlist row for every seeded ticker" 10 \
    "$(curl -s "${BASE}/api/watchlist" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["tickers"]))')"
check "the header has a portfolio to render" 200 "$(code "${BASE}/api/portfolio")"

echo "Every panel is in the export"
# The page is a client component, but `output: 'export'` still prerenders it,
# so each panel's static chrome is in the HTML. A panel that failed to mount
# would be absent here rather than merely empty on screen.
for PANEL in Watchlist "Market Feed" Positions Allocation "Portfolio value" Trade Assistant; do
    contains "the ${PANEL} panel is rendered" "${PANEL}" "${TMP}/index.html"
done
contains "the watchlist has an add control" "Add ticker" "${TMP}/index.html"
contains "the chart asks for a selection" "Select a ticker" "${TMP}/index.html"

echo "The endpoints the new panels read"
check "GET /api/portfolio/history" 200 "$(code "${BASE}/api/portfolio/history")"
check "  it returns a snapshot array" true \
    "$(curl -s "${BASE}/api/portfolio/history" | python3 -c 'import json,sys; print(isinstance(json.load(sys.stdin)["snapshots"], list))' | tr "A-Z" "a-z")"
check "  a limit of zero is a 422" 422 "$(code "${BASE}/api/portfolio/history?limit=0")"

echo "The trade bar's round trip"
POINTS_BEFORE="$(curl -s "${BASE}/api/portfolio/history?limit=5000" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["snapshots"]))')"
trade() { # trade <json>
    curl -s -X POST "${BASE}/api/portfolio/trade" -H 'Content-Type: application/json' -d "$1"
}
trade_code() { curl -s -o /dev/null -w '%{http_code}' -X POST "${BASE}/api/portfolio/trade" -H 'Content-Type: application/json' -d "$1"; }

check "a buy fills" 201 "$(trade_code '{"ticker":"AAPL","side":"buy","quantity":3}')"
trade '{"ticker":"AAPL","side":"buy","quantity":2}' >"${TMP}/fill.json"
check "  the fill carries a server-side price" true \
    "$(python3 -c 'import json;d=json.load(open("'"${TMP}"'/fill.json"));print(d["trade"]["price"] > 0)' | tr "A-Z" "a-z")"
check "  and the portfolio it left behind" true \
    "$(python3 -c 'import json;d=json.load(open("'"${TMP}"'/fill.json"));print(any(p["ticker"]=="AAPL" for p in d["portfolio"]["positions"]))' | tr "A-Z" "a-z")"
check "  cash fell by the fill value" true \
    "$(python3 -c 'import json;d=json.load(open("'"${TMP}"'/fill.json"));print(d["portfolio"]["cash_balance"] < 10000)' | tr "A-Z" "a-z")"

# The P&L chart's exit criterion: the trade's own snapshot is there at once,
# without waiting for the 30-second task.
POINTS_AFTER="$(curl -s "${BASE}/api/portfolio/history?limit=5000" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["snapshots"]))')"
check "each trade wrote a snapshot immediately" true \
    "$([ "${POINTS_AFTER}" -ge "$((POINTS_BEFORE + 2))" ] && echo true || echo false)"

echo "The rejections the trade bar has to show"
check "insufficient cash is a 400" 400 "$(trade_code '{"ticker":"AAPL","side":"buy","quantity":100000}')"
contains "  it carries a reason to display" "detail" <(trade '{"ticker":"AAPL","side":"buy","quantity":100000}')
check "overselling is a 400" 400 "$(trade_code '{"ticker":"AAPL","side":"sell","quantity":99999}')"
check "a client-named price is a 422" 422 \
    "$(trade_code '{"ticker":"AAPL","side":"buy","quantity":1,"price":1}')"
check "a zero quantity is a 422" 422 "$(trade_code '{"ticker":"AAPL","side":"buy","quantity":0}')"

echo "The watchlist controls"
check "adding a ticker" 201 \
    "$(curl -s -o /dev/null -w '%{http_code}' -X POST "${BASE}/api/watchlist" -H 'Content-Type: application/json' -d '{"ticker":"pypl"}')"
contains "  it joins the list" '"PYPL"' <(curl -s "${BASE}/api/watchlist")
check "adding it twice is a 409" 409 \
    "$(curl -s -o /dev/null -w '%{http_code}' -X POST "${BASE}/api/watchlist" -H 'Content-Type: application/json' -d '{"ticker":"PYPL"}')"
check "removing a ticker" 200 \
    "$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "${BASE}/api/watchlist/PYPL")"
check "removing it again is a 404" 404 \
    "$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "${BASE}/api/watchlist/PYPL")"
# The one that matters for the positions table: a held ticker keeps streaming.
check "removing a held ticker keeps it tracked" true \
    "$(curl -s -X DELETE "${BASE}/api/watchlist/AAPL" | python3 -c 'import json,sys; print(json.load(sys.stdin)["still_tracked"])' | tr "A-Z" "a-z")"
check "  and keeps the position" true \
    "$(curl -s "${BASE}/api/portfolio" | python3 -c 'import json,sys; print(any(p["ticker"]=="AAPL" for p in json.load(sys.stdin)["positions"]))' | tr "A-Z" "a-z")"

echo "The assistant"
chat() { curl -s -X POST "${BASE}/api/chat" -H 'Content-Type: application/json' -d "$1"; }
chat_code() { curl -s -o /dev/null -w '%{http_code}' -X POST "${BASE}/api/chat" -H 'Content-Type: application/json' -d "$1"; }

check "GET /api/chat/history" 200 "$(code "${BASE}/api/chat/history")"
check "an empty message is a 422" 422 "$(chat_code '{"message":"   "}')"

chat '{"message":"buy 2 NVDA"}' >"${TMP}/chat.json"
check "a turn executes and reports the fill" true \
    "$(python3 -c 'import json;d=json.load(open("'"${TMP}"'/chat.json"));a=d["actions"][0];print(a["kind"]=="trade" and a["ok"] is True and a["ticker"]=="NVDA")' | tr "A-Z" "a-z")"
check "  and carries the resulting portfolio" true \
    "$(python3 -c 'import json;d=json.load(open("'"${TMP}"'/chat.json"));print(any(p["ticker"]=="NVDA" for p in d["portfolio"]["positions"]))' | tr "A-Z" "a-z")"

# The panel's whole reason for existing: the model writes its message before it
# knows whether anything cleared, so the refusal must come back as an action.
chat '{"message":"buy 100000 AAPL"}' >"${TMP}/refused.json"
check "a refused action comes back as ok:false" true \
    "$(python3 -c 'import json;d=json.load(open("'"${TMP}"'/refused.json"));a=d["actions"][0];print(a["ok"] is False and "Insufficient cash" in a["detail"])' | tr "A-Z" "a-z")"
check "  while the reply is still a 200" 200 "$(chat_code '{"message":"buy 100000 AAPL"}')"

chat '{"message":"watch PYPL"}' >/dev/null
check "an assistant watchlist add reaches the list" true \
    "$(curl -s "${BASE}/api/watchlist" | python3 -c 'import json,sys; print(any(t["ticker"]=="PYPL" for t in json.load(sys.stdin)["tickers"]))' | tr "A-Z" "a-z")"

# What a page reload replays.
curl -s "${BASE}/api/chat/history?limit=500" >"${TMP}/transcript.json"
check "every turn persists, both halves" true \
    "$(python3 -c 'import json;m=json.load(open("'"${TMP}"'/transcript.json"))["messages"];print(len(m) >= 8 and m[0]["role"]=="user")' | tr "A-Z" "a-z")"
check "  and a refusal replays as a refusal" true \
    "$(python3 -c '
import json
m = json.load(open("'"${TMP}"'/transcript.json"))["messages"]
refused = [a for t in m if t["actions"] for a in t["actions"] if not a["ok"]]
print(len(refused) > 0 and "Insufficient cash" in refused[0]["detail"])
' | tr "A-Z" "a-z")"

echo
if [ "$FAILURES" = 0 ]; then
    echo "All checks passed."
else
    echo "${FAILURES} check(s) failed."
fi
exit "$FAILURES"
