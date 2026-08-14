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
RUNS="${1:-1}"
BUILD="--build"
[ "${2:-}" = "--no-build" ] && BUILD=""

if ! docker info >/dev/null 2>&1; then
    echo "Docker is not running." >&2
    exit 1
fi

cleanup() {
    docker compose -f "$COMPOSE" down --remove-orphans >/dev/null 2>&1
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
