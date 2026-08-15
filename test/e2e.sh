#!/usr/bin/env bash
# Gate 3 for Checkpoint 9: the §12 end-to-end suite, in containers.
#
#   test/e2e.sh            # one run
#   test/e2e.sh 3          # three consecutive runs — the exit criterion
#   test/e2e.sh 1 --no-build
#
# Wraps the command PLAN.md names:
#
#   docker compose -f test/docker-compose.test.yml up --abort-on-container-exit
#
# with two additions that are not cosmetic:
#
#   --exit-code-from playwright  `--abort-on-container-exit` alone stops the
#       stack but reports *its own* success, so a suite of failing specs exits
#       zero. Every run here is judged by the runner's exit code.
#   down -v between runs         each run starts from a fresh, seeded database.
#       The app containers mount no volume, but a stopped container keeps its
#       filesystem, and `up` would restart it with the previous run's trades.
#
# Exits non-zero on the first failing run.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="${REPO}/test/docker-compose.test.yml"
RUNS=1
BUILD="--build"

# Order-independent, and a bad argument is refused rather than absorbed. The
# first version read the run count from $1 and the flag from $2, so
# `test/e2e.sh --no-build` ran `seq 1 --no-build`, executed the loop body zero
# times, printed "0 consecutive run(s) passed" and exited 0 — a green result
# from a suite that never started.
for arg in "$@"; do
    case "$arg" in
        --no-build) BUILD="" ;;
        ''|*[!0-9]*)
            echo "Usage: test/e2e.sh [runs] [--no-build]" >&2
            exit 2
            ;;
        *) RUNS="$arg" ;;
    esac
done

if [ "$RUNS" -lt 1 ]; then
    echo "runs must be at least 1" >&2
    exit 2
fi

if ! docker info >/dev/null 2>&1; then
    echo "Docker is not running." >&2
    exit 1
fi

cleanup() {
    # -v as the header says. Container removal is what gives each run a fresh
    # database — the app services mount nothing — but the runner's anonymous
    # node_modules volume is not a container, and without this a three-run
    # invocation left three dangling volumes behind. The volume is recreated
    # from the image on the next `up`, so nothing is lost by removing it.
    docker compose -f "$COMPOSE" down --volumes --remove-orphans >/dev/null 2>&1
}
trap cleanup EXIT

for run in $(seq 1 "$RUNS"); do
    echo "=== run ${run} of ${RUNS} ==============================================="
    cleanup

    # shellcheck disable=SC2086  # BUILD is deliberately word-split or empty
    docker compose -f "$COMPOSE" up $BUILD \
        --abort-on-container-exit --exit-code-from playwright
    status=$?

    if [ "$status" -ne 0 ]; then
        echo
        echo "Run ${run} failed with exit code ${status}."
        echo "The HTML report is in test/e2e/report; traces are attached to failures."
        exit "$status"
    fi

    # The image is built once; later runs reuse it.
    BUILD=""
done

echo
echo "${RUNS} consecutive run(s) passed."
