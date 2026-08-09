"""Pytest configuration and fixtures."""

import pytest


@pytest.fixture
def event_loop_policy():
    """Use the default event loop policy for all async tests."""
    import asyncio

    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Point every test at a throwaway database.

    Autouse and unconditional. `app.db` falls back to the repo's real
    `db/finally.db` when DB_PATH is unset, so without this a test that
    initialises or writes would quietly mutate the developer's own portfolio —
    and a test asserting on seed data would pass or fail depending on what the
    developer had traded that afternoon.
    """
    path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(path))
    return path


@pytest.fixture(autouse=True)
def no_massive_key(monkeypatch):
    """Force the simulator, whatever the developer has in their environment.

    A real MASSIVE_API_KEY in the shell would otherwise send the app tests over
    the network on every run.
    """
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
