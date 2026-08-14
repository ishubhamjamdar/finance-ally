"""The packaging contract: Dockerfile, compose file, and the four scripts.

These files are not Python, and Checkpoint 8's real verification is
`test/smoke_docker.sh`, which builds the image and runs it. So why assert
anything here?

Because three of the failures this checkpoint can produce are *silent*. A
`.dockerignore` that stops excluding `.env` bakes an API key into a layer, and
the image still works. A `DB_PATH` that stops matching the volume mount writes
the database into the container's own filesystem, and the app still works —
until `stop` and `start`, when the portfolio is gone. A scripts/compose pair
that disagrees about the volume name gives the user two databases, and both
front doors still work. None of that fails a build, and only one of them fails
a smoke test that does not think to look.

The other reason is Checkpoint 5's lesson, which cost a clean clone that could
not build: the `lib/` entry in `.gitignore` silently excluded six source files
while every local check passed against the untracked copies on disk. Every file
this checkpoint adds is therefore asserted to be *tracked by git*, not merely
present.
"""

from __future__ import annotations

import re
import subprocess

import pytest

from app.paths import REPO_ROOT, is_source_checkout

pytestmark = pytest.mark.skipif(
    not is_source_checkout(),
    reason="packaging files live in the repo, which an installed package has no view of",
)

#: Everything Checkpoint 8 adds at the repo root, by path.
PACKAGING_FILES = (
    "Dockerfile",
    ".dockerignore",
    "docker-compose.yml",
    ".env.example",
    "scripts/start_mac.sh",
    "scripts/stop_mac.sh",
    "scripts/start_windows.ps1",
    "scripts/stop_windows.ps1",
    "test/smoke_docker.sh",
)

#: The names the image, container, volume and port are known by. The scripts,
#: the compose file and the Dockerfile each spell them out; if they ever
#: disagree, `docker compose up` and `start_mac.sh` become two deployments with
#: two databases.
CONTAINER_NAME = "finally"
VOLUME_NAME = "finally-data"
CONTAINER_PORT = "8000"
DB_DIR = "/app/db"


def read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text()


def strip_comments(relative: str, body: str) -> str:
    """The script with its prose removed, so an assertion about what a script
    *does* is not satisfied — or defeated — by what it says.

    PowerShell's `<# ... #>` help block is the reason this exists: both stop
    scripts document `docker volume rm` as the deliberate manual escape, and a
    naive line scan reads that as the script removing the volume.
    """
    if relative.endswith(".ps1"):
        body = re.sub(r"<#.*?#>", "", body, flags=re.DOTALL)
    return "\n".join(line for line in body.splitlines() if not line.strip().startswith("#"))


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return read("Dockerfile")


@pytest.fixture(scope="module")
def compose() -> str:
    return read("docker-compose.yml")


class TestEverythingIsCommitted:
    """A clean clone must contain all of it — Checkpoint 8's first exit
    criterion is a build from one."""

    @pytest.mark.parametrize("relative", PACKAGING_FILES)
    def test_the_file_exists(self, relative):
        assert (REPO_ROOT / relative).is_file(), f"{relative} is missing"

    @pytest.mark.parametrize("relative", PACKAGING_FILES)
    def test_git_tracks_the_file(self, relative):
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", relative],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert tracked.returncode == 0, (
            f"{relative} exists on disk but git does not track it — "
            "a clean clone would not have it (see Checkpoint 5's `lib/`)"
        )

    def test_the_example_env_is_not_ignored_along_with_the_real_one(self):
        """`.gitignore` carries `.env`, and `.env.example` must survive it."""
        ignored = subprocess.run(
            ["git", "check-ignore", ".env.example"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert ignored.returncode != 0, ".env.example is gitignored"


class TestSecretsStayOutOfTheImage:
    def test_the_build_context_excludes_dotenv(self):
        patterns = {
            line.strip()
            for line in read(".dockerignore").splitlines()
            if line.strip() and not line.startswith("#")
        }
        assert ".env" in patterns, (
            ".dockerignore no longer excludes .env — the OpenRouter key would "
            "enter the build context, and any COPY that reaches it bakes the "
            "key into a layer that `docker history` prints"
        )

    def test_the_example_env_is_allowed_through(self):
        """The exclusion is `.env` and `.env.*`, which would otherwise take the
        committed template with it. Not a secret, and the README points at it."""
        assert "!.env.example" in read(".dockerignore")

    def test_no_env_file_is_copied_into_the_image(self, dockerfile):
        copies = re.findall(r"^COPY\s+(.*)$", dockerfile, flags=re.MULTILINE)
        assert not any(".env" in line for line in copies), f"a COPY names .env: {copies}"

    def test_the_frontend_bundle_is_built_with_an_empty_api_base(self, dockerfile):
        """NEXT_PUBLIC_API_BASE is inlined at build time (PLAN.md §5). Empty is
        the production value, and anything else would be frozen into the
        JavaScript every user downloads."""
        assert re.search(r'^ENV NEXT_PUBLIC_API_BASE=""$', dockerfile, flags=re.MULTILINE), (
            "the frontend stage must pin NEXT_PUBLIC_API_BASE to empty"
        )


class TestThePersistencePathIsConsistent:
    """PLAN.md §11: the SQLite file lives in a named volume. That is three
    independent statements — where the app writes, what the image owns, and
    what the run mounts — and it only works if all three agree."""

    def test_the_image_writes_the_database_into_the_mount_point(self, dockerfile):
        match = re.search(r"^ENV DB_PATH=(\S+)", dockerfile, flags=re.MULTILINE)
        assert match, "the Dockerfile does not set DB_PATH"
        assert match.group(1).startswith(f"{DB_DIR}/"), (
            f"DB_PATH={match.group(1)} is outside {DB_DIR}, so the database would "
            "be written into the container's own filesystem and lost on stop/start"
        )

    def test_the_image_serves_the_export_it_copied(self, dockerfile):
        assert re.search(r"^\s*STATIC_DIR=/app/static", dockerfile, flags=re.MULTILINE)
        assert re.search(r"^COPY --from=frontend \S+ \./static$", dockerfile, flags=re.MULTILINE)

    def test_the_mount_point_is_owned_by_the_runtime_user(self, dockerfile):
        """A fresh named volume inherits the ownership of the image directory it
        covers. Created after the chown — or not created at all — it arrives
        owned by root and the first write fails."""
        setup = re.search(r"RUN useradd.*?\n(?:.*\\\n)*.*", dockerfile, flags=re.DOTALL)
        assert setup, "no useradd/chown block"
        block = setup.group(0)
        assert f"mkdir -p {DB_DIR}" in block
        assert "chown -R" in block
        assert block.index("mkdir") < block.index("chown"), "the chown must follow the mkdir"

    def test_the_container_does_not_run_as_root(self, dockerfile):
        user = re.search(r"^USER\s+(\S+)", dockerfile, flags=re.MULTILINE)
        assert user and user.group(1) != "root"
        assert dockerfile.index("USER ") < dockerfile.index("CMD "), "USER must precede CMD"

    def test_compose_mounts_the_same_volume_at_the_same_place(self, compose):
        assert f"{VOLUME_NAME}:{DB_DIR}" in compose

    def test_compose_names_the_volume_explicitly(self, compose):
        """Left to itself Compose prefixes the project name and creates
        `finally_finally-data` — a second, empty database beside the scripts'."""
        assert re.search(rf"^\s+name:\s+{VOLUME_NAME}$", compose, flags=re.MULTILINE)

    @pytest.mark.parametrize("script", ["scripts/stop_mac.sh", "scripts/stop_windows.ps1"])
    def test_the_scripts_never_remove_the_volume(self, script):
        """`stop` must leave the portfolio behind — that is the difference
        between stopping the app and deleting the account. Both scripts *name*
        `docker volume rm` in their header, as the documented manual escape, so
        the assertion is on the code with the comments taken out."""
        assert "volume rm" not in strip_comments(script, read(script))


class TestTheFrontDoorsAgree:
    @pytest.mark.parametrize("script", ["scripts/start_mac.sh", "scripts/start_windows.ps1"])
    def test_the_start_scripts_use_the_shared_names(self, script):
        body = read(script)
        assert CONTAINER_NAME in body
        assert VOLUME_NAME in body
        assert f"{DB_DIR}" in body

    @pytest.mark.parametrize("script", ["scripts/start_mac.sh", "scripts/start_windows.ps1"])
    def test_the_host_port_is_overridable_and_the_container_port_is_not(self, script):
        body = read(script)
        assert "FINALLY_PORT" in body, "a host with 8000 taken must have a way out"
        assert f":{CONTAINER_PORT}" in body

    def test_compose_publishes_the_container_port(self, compose):
        assert re.search(rf'"\$\{{FINALLY_PORT:-{CONTAINER_PORT}\}}:{CONTAINER_PORT}"', compose)

    def test_compose_tolerates_a_missing_env_file(self, compose):
        """PLAN.md §11 and Checkpoint 8's last exit criterion: the app runs with
        no `.env` at all. A hard `env_file` entry makes Compose refuse to start."""
        assert "required: false" in compose

    @pytest.mark.parametrize("script", ["scripts/start_mac.sh", "scripts/stop_mac.sh"])
    def test_the_shell_scripts_are_executable_with_a_shebang(self, script):
        path = REPO_ROOT / script
        assert path.read_text().startswith("#!/usr/bin/env bash")
        assert path.stat().st_mode & 0o111, f"{script} is not executable"

    @pytest.mark.parametrize("script", ["scripts/start_mac.sh", "scripts/stop_mac.sh"])
    def test_the_shell_scripts_fail_loudly(self, script):
        assert "set -euo pipefail" in read(script)


class TestTheExampleEnvDocumentsSectionFive:
    """PLAN.md §5 lists seven variables. A `.env.example` missing one is how a
    user ends up not knowing an option exists."""

    @pytest.mark.parametrize(
        "variable",
        [
            "OPENROUTER_API_KEY",
            "MASSIVE_API_KEY",
            "LLM_MOCK",
            "DB_PATH",
            "STATIC_DIR",
            "LOG_LEVEL",
            "NEXT_PUBLIC_API_BASE",
        ],
    )
    def test_the_variable_is_documented(self, variable):
        assert variable in read(".env.example")

    def test_no_key_is_committed_in_the_template(self):
        """Every assignment in the template is empty or a safe default — a real
        key pasted here is a key in the git history."""
        for line in read(".env.example").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            name, _, value = stripped.partition("=")
            assert value in ("", "false", "INFO"), f"{name} has a value in .env.example: {value!r}"
