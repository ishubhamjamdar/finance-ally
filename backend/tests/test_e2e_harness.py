"""The end-to-end harness's own contract: `test/docker-compose.test.yml` and
`test/e2e/`.

The suite these describe runs in Docker and takes minutes. These are the checks
worth making in milliseconds, and every one of them guards a failure that is
either silent or expensive:

- **A Playwright version that does not match its image tag** fails at run time
  with "browser not found", after the image has been pulled and the app built.
- **A key leaking into the test environment** would make "no test depends on a
  real OpenRouter or Massive key" — an exit criterion — untrue in a way that
  only shows up as a surprise bill or a CI failure on someone else's fork.
- **A volume on the test app** would carry one run's trades into the next, and
  the fresh-start assertions would pass or fail depending on history.
- **Retries** would turn the intermittency this suite exists to catch into a
  green run, which is exactly what Checkpoint 9's review focus warns about.
"""

from __future__ import annotations

import json
import re
import subprocess

import pytest

from app.paths import REPO_ROOT, is_source_checkout

pytestmark = pytest.mark.skipif(
    not is_source_checkout(),
    reason="the harness lives in the repo, which an installed package has no view of",
)

E2E = "test/e2e"

#: Every scenario PLAN.md §12 names, and the spec file that owns it. A scenario
#: with no file is a gap; a file with no scenario is scope creep. Both are
#: easier to see written down than to notice in a directory listing.
SCENARIOS = {
    "fresh start": "fresh-start.spec.ts",
    "watchlist add and remove": "watchlist.spec.ts",
    "buy shares": "trading.spec.ts",
    "sell shares": "trading.spec.ts",
    "portfolio visualisation": "portfolio-visualisation.spec.ts",
    "mocked chat with a trade": "chat.spec.ts",
    "SSE reconnection": "sse-resilience.spec.ts",
    # Not in §12: the layout measurements Checkpoints 6 and 7 carried forward
    # to this checkpoint after three defects that only a browser could see.
    "layout at three widths": "layout.spec.ts",
}

HARNESS_FILES = (
    "test/docker-compose.test.yml",
    f"{E2E}/Dockerfile",
    f"{E2E}/package.json",
    f"{E2E}/package-lock.json",
    f"{E2E}/playwright.config.ts",
    f"{E2E}/specs/helpers.ts",
    *(f"{E2E}/specs/{name}" for name in sorted(set(SCENARIOS.values()))),
)


def read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text()


@pytest.fixture(scope="module")
def compose() -> str:
    return read("test/docker-compose.test.yml")


class TestTheHarnessIsCommitted:
    @pytest.mark.parametrize("relative", HARNESS_FILES)
    def test_git_tracks_the_file(self, relative):
        """Checkpoint 5's `lib/` lesson: on disk is not the same as committed."""
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", relative],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert tracked.returncode == 0, f"{relative} is not tracked — a clone would not have it"

    @pytest.mark.parametrize("scenario,spec", sorted(SCENARIOS.items()))
    def test_the_scenario_has_a_spec(self, scenario, spec):
        assert (REPO_ROOT / E2E / "specs" / spec).is_file(), f"{scenario} has no spec ({spec})"


class TestTheRunnerMatchesItsBrowsers:
    def test_the_image_tag_matches_the_installed_playwright(self):
        """`mcr.microsoft.com/playwright:vX-noble` ships the browser builds
        that version X expects. A mismatch is not a subtle degradation — it is
        "Executable doesn't exist", after the pull and the build."""
        dockerfile = read(f"{E2E}/Dockerfile")
        tag = re.search(r"FROM mcr\.microsoft\.com/playwright:v([\d.]+)-", dockerfile)
        assert tag, "the runner does not use a pinned Playwright image"

        manifest = json.loads(read(f"{E2E}/package.json"))
        installed = manifest["devDependencies"]["@playwright/test"]
        assert installed == tag.group(1), (
            f"@playwright/test is {installed} but the image is v{tag.group(1)}"
        )

    def test_the_playwright_dependency_is_pinned_exactly(self):
        """No `^`: a minor bump would silently diverge from the image tag, and
        the test above would be the only thing that noticed — after a rebuild."""
        manifest = json.loads(read(f"{E2E}/package.json"))
        assert re.fullmatch(r"[\d.]+", manifest["devDependencies"]["@playwright/test"])


class TestTheSuiteNeedsNoSecrets:
    """PLAN.md §Checkpoint 9: "No test depends on a real OpenRouter or Massive
    key." Three ways that could stop being true, all three closed."""

    def test_the_mock_model_is_selected(self, compose):
        assert re.search(r'LLM_MOCK:\s*"true"', compose)

    @pytest.mark.parametrize("variable", ["OPENROUTER_API_KEY", "MASSIVE_API_KEY"])
    def test_the_key_is_pinned_empty_rather_than_inherited(self, variable, compose):
        """Present and empty, not absent. An unset variable is inherited from
        whoever runs the suite, which makes a "no network" run capable of
        calling OpenRouter on a developer's key without anyone choosing that."""
        assert re.search(rf'{variable}:\s*""', compose), f"{variable} is not pinned empty"

    def test_no_env_file_is_read(self, compose):
        assert "env_file" not in compose, "the test app must not read a developer's .env"


class TestEachRunStartsClean:
    def test_the_test_app_mounts_no_volume(self, compose):
        """An ephemeral database per `up`. With a volume, one run's trades are
        the next run's starting balance, and the fresh-start spec would pass
        exactly once."""
        assert not re.search(r"^\s+volumes:\s*$", compose.split("services:")[0], re.MULTILINE)
        service_block = compose.split("services:")[1]
        app_volumes = re.findall(r":/app/db", service_block)
        assert app_volumes == [], "the test app has a volume mounted at /app/db"

    def test_there_is_a_pristine_app_for_the_fresh_start_assertions(self, compose):
        """Two app services, so "a fresh start shows $10,000" is a fact about a
        clean database rather than about this file running first."""
        assert "app-pristine:" in compose
        assert "PRISTINE_URL:" in compose


class TestTheSuiteCannotHideIntermittency:
    """Checkpoint 9's review focus is flaky-test sources. These are the three
    settings that would convert a flake into a green run."""

    @pytest.fixture(scope="class")
    def config(self) -> str:
        return read(f"{E2E}/playwright.config.ts")

    def test_no_retries(self, config):
        assert re.search(r"retries:\s*0", config), (
            "a retry turns 'fails one run in five' into 'passes', which is the "
            "opposite of what three consecutive clean runs are meant to prove"
        )

    def test_one_worker(self, config):
        """The app is single-user by design — one profile row, one watchlist.
        Two workers would be two people trading one account."""
        assert re.search(r"workers:\s*1", config)
        assert re.search(r"fullyParallel:\s*false", config)

    def test_the_runner_forbids_a_stray_only(self):
        """`test.only` left in a spec reduces the suite to one test and still
        exits zero — the quietest possible way to stop testing anything."""
        assert "--forbid-only" in read(f"{E2E}/Dockerfile")

    def test_no_spec_sleeps_for_a_fixed_duration(self):
        """Checkpoint 5's follow-up pass hunted one flake for two checkpoints
        and found a fixed sleep. Specs wait for conditions: `expect.poll`,
        `toHaveText`, `waitFor`. The one exception is the chat spec, which
        delays a *route* to make a loading state observable — that is slowing
        the server down, not guessing how long the client needs."""
        offenders = []
        for spec in (REPO_ROOT / E2E / "specs").glob("*.spec.ts"):
            for number, line in enumerate(spec.read_text().splitlines(), start=1):
                if "waitForTimeout" in line:
                    offenders.append(f"{spec.name}:{number}")
        assert not offenders, f"fixed sleeps in specs: {offenders}"
