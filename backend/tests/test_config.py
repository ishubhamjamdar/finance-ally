"""Tests for `app.config` — loading `.env`.

PLAN.md §5 has promised this since Checkpoint 1 and nothing implemented it
until Checkpoint 4, when `OPENROUTER_API_KEY` became the first variable with no
working default. The precedence is what the tests are mostly about: a checked
-out `.env` must never override what Docker or the shell already set.
"""

from __future__ import annotations

import os

import pytest

from app.config import load_env

#: The variable these tests write. `load_dotenv` sets it behind monkeypatch's
#: back — monkeypatch can undo a `setenv` it performed, not one a library
#: performed — so it is cleaned up explicitly rather than leaking into whatever
#: test runs next.
_TEST_VAR = "FINALLY_TEST_KEY"


@pytest.fixture(autouse=True)
def clean_test_var():
    os.environ.pop(_TEST_VAR, None)
    yield
    os.environ.pop(_TEST_VAR, None)


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """A repo-shaped directory `app.config` will accept as a source checkout."""
    (tmp_path / "backend" / "app").mkdir(parents=True)
    monkeypatch.setattr("app.config.REPO_ROOT", tmp_path)
    monkeypatch.setattr("app.config.is_source_checkout", lambda: True)
    return tmp_path


class TestLoading:
    def test_it_reads_variables_from_the_repo_root(self, fake_repo, monkeypatch):
        (fake_repo / ".env").write_text("FINALLY_TEST_KEY=from-the-file\n")
        monkeypatch.delenv(_TEST_VAR, raising=False)

        assert load_env() is True
        assert os.environ[_TEST_VAR] == "from-the-file"

    def test_an_absent_file_is_not_an_error(self, fake_repo):
        """The image bakes no `.env` — PLAN.md §11 passes the variables in with
        `--env-file` instead — so absent is the normal container case."""
        assert load_env() is False

    def test_it_does_not_look_outside_a_source_checkout(self, tmp_path, monkeypatch):
        """`REPO_ROOT` is only meaningful in a checkout; in the image it is
        wherever the installed package happens to sit two directories up."""
        (tmp_path / ".env").write_text("FINALLY_TEST_KEY=should-not-load\n")
        monkeypatch.setattr("app.config.REPO_ROOT", tmp_path)
        monkeypatch.setattr("app.config.is_source_checkout", lambda: False)
        monkeypatch.delenv(_TEST_VAR, raising=False)

        assert load_env() is False
        assert _TEST_VAR not in os.environ

    def test_a_directory_named_env_is_not_read(self, fake_repo):
        (fake_repo / ".env").mkdir()

        assert load_env() is False


class TestPrecedence:
    def test_the_environment_wins_over_the_file(self, fake_repo, monkeypatch):
        """Docker `--env-file`, a shell export and a test's `monkeypatch.setenv`
        all beat the checked-out file. Without this a stale `.env` in a source
        tree could silently redirect a container's database or key."""
        (fake_repo / ".env").write_text("FINALLY_TEST_KEY=from-the-file\n")
        monkeypatch.setenv(_TEST_VAR, "from-the-environment")

        load_env()

        assert os.environ[_TEST_VAR] == "from-the-environment"

    def test_loading_twice_changes_nothing(self, fake_repo, monkeypatch):
        """`app.main` calls it at import; a reload under `--reload` must not
        clobber a value the operator set since."""
        (fake_repo / ".env").write_text("FINALLY_TEST_KEY=from-the-file\n")
        monkeypatch.delenv(_TEST_VAR, raising=False)

        load_env()
        os.environ[_TEST_VAR] = "changed-since"
        load_env()

        assert os.environ[_TEST_VAR] == "changed-since"


def test_the_real_app_loads_the_environment_at_import():
    """`app.main` must call this before `logging.basicConfig` and before any
    module reads the environment — a `LOG_LEVEL` or `OPENROUTER_API_KEY` loaded
    afterwards would be read too late to have any effect."""
    import inspect

    import app.main

    source = inspect.getsource(app.main)
    assert source.index("load_env()") < source.index("logging.basicConfig")
