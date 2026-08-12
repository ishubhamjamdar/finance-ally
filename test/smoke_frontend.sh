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
DB_PATH="${TMP}/finally.db" MASSIVE_API_KEY= STATIC_DIR="${FRONTEND}/out" \
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

echo
if [ "$FAILURES" = 0 ]; then
    echo "All checks passed."
else
    echo "${FAILURES} check(s) failed."
fi
exit "$FAILURES"
