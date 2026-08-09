"""Pytest configuration and fixtures shared across the suite."""

import uuid

import pytest

# PLAN.md §7 spells these out. Written here as a literal rather than imported
# from app.market, so that a change to the market module's seed list cannot
# silently redefine what the plan says the default watchlist is. One literal
# for the whole suite — a second copy could disagree with this one and both
# would still pass.
PLAN_DEFAULT_WATCHLIST = (
    "AAPL",
    "GOOGL",
    "MSFT",
    "AMZN",
    "TSLA",
    "NVDA",
    "META",
    "JPM",
    "V",
    "NFLX",
)


@pytest.fixture
def event_loop_policy():
    """Use the default event loop policy for all async tests."""
    import asyncio

    return asyncio.DefaultEventLoopPolicy()


class TempDb:
    """The test database, and the operations tests perform on it as a file.

    Deleting it means deleting the WAL sidecars too. That glob lived in two
    test modules with the filename hardcoded; had the fixture's name ever
    changed, the glob would have matched nothing, `assert not exists()` would
    still have passed, and both "the database is rebuilt" tests would have
    quietly stopped testing anything.
    """

    def __init__(self, path):
        self.path = path

    def exists(self) -> bool:
        return self.path.exists()

    def delete(self) -> None:
        for sidecar in self.path.parent.glob(self.path.name + "*"):
            sidecar.unlink()

    def __fspath__(self) -> str:
        return str(self.path)

    def __eq__(self, other) -> bool:
        return self.path == other

    def __repr__(self) -> str:
        return f"TempDb({self.path})"


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
    return TempDb(path)


@pytest.fixture(autouse=True)
def no_massive_key(monkeypatch):
    """Force the simulator, whatever the developer has in their environment.

    A real MASSIVE_API_KEY in the shell would otherwise send the app tests over
    the network on every run.
    """
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)


@pytest.fixture
def add_position():
    """Insert a position row. Shared: CP3 adds writers to this table, and two
    copies of the raw INSERT would both need to follow every schema change."""
    from app.db import DEFAULT_USER_ID, connect, utc_now

    def _add(ticker: str, quantity: float = 5.0, user_id: str = DEFAULT_USER_ID) -> None:
        with connect() as conn:
            conn.execute(
                "INSERT INTO positions (id, user_id, ticker, quantity, avg_cost, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), user_id, ticker, quantity, 100.0, utc_now()),
            )

    return _add
