"""Pytest configuration and fixtures shared across the suite."""

import json
import uuid
from pathlib import Path

import pytest

from app.market import MarketDataSource, PriceCache

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


#: Every environment variable the application reads, except `DB_PATH`, which
#: `temp_db` sets to a throwaway file of its own.
#:
#: All of them are cleared for every test. From Checkpoint 4 on, `app.main`
#: loads the repo's `.env` at *import*, and an import-time mutation of
#: `os.environ` is not something `monkeypatch` can undo — so without this a
#: developer with `SIM_UPDATE_INTERVAL` or `MASSIVE_POLL_INTERVAL` in their
#: `.env` would run a quietly different suite from CI, and find out at review
#: time. The list is exhaustive on purpose: a new variable that is not added
#: here is one whose value the suite inherits from whoever is running it.
_APP_ENV_VARS = (
    "LLM_MOCK",
    "LOG_LEVEL",
    "MASSIVE_API_KEY",
    "MASSIVE_POLL_INTERVAL",
    "OPENROUTER_API_KEY",
    "SIM_EVENT_PROBABILITY",
    "SIM_UPDATE_INTERVAL",
    "STATIC_DIR",
)

#: What `STATIC_DIR` is pinned to for the suite. It must not exist.
#:
#: Deleting the variable is *not* neutral for this one: its default is a
#: filesystem search that ends at `frontend/out`, so from Checkpoint 5 on, a
#: developer who has run `npm run build` gets `StaticFiles` mounted at "/" and
#: a materially different app — one where every unmatched `/api/*` path is
#: answered by the static handler instead of by FastAPI. That is a 405 rather
#: than a 404 for a method it does not serve, and it failed a real test.
_NO_STATIC_BUILD = Path(__file__).parent / "no-frontend-build"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    """Run every test against defaults, whatever is in the shell or in `.env`.

    Clearing `MASSIVE_API_KEY` forces the simulator — a real key would send the
    app tests over the network on every run. Clearing `OPENROUTER_API_KEY` and
    `LLM_MOCK` together makes an unmocked LLM call *impossible* rather than
    merely unlikely: with neither set, `complete()` raises
    `LLMUnavailableError` before it can open a socket, so a test that reached it
    by accident fails loudly instead of spending money. A test that wants the
    mock opts in through the `mock_llm` fixture.

    `STATIC_DIR` is pinned rather than cleared — see `_NO_STATIC_BUILD`. Tests
    that want the frontend mounted set it to a `tmp_path` of their own.
    """
    for name in _APP_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("STATIC_DIR", str(_NO_STATIC_BUILD))


@pytest.fixture
def mock_llm(monkeypatch):
    """Select the deterministic mock (PLAN.md §5's `LLM_MOCK=true`)."""
    monkeypatch.setenv("LLM_MOCK", "true")


class StubModel:
    """Stands in for the provider, patched over `app.chat.complete`.

    Patched at the *call site* rather than inside `app.llm.client`, so a test
    can hand `app.chat` any raw string at all — including text no real provider
    would produce — and watch it travel through the real `parse_reply` and the
    real execution path. That is the only way to drive the malformed-reply
    branches, which is where PLAN.md §Checkpoint 4's "never a 500" lives.
    """

    def __init__(self) -> None:
        self.raw = json.dumps({"message": "Noted.", "trades": [], "watchlist_changes": []})
        self.error: Exception | None = None
        self.messages: list[dict] | None = None
        self.calls = 0

    def replies(self, message="Noted.", trades=None, watchlist_changes=None) -> None:
        """Answer with a well-formed reply carrying these actions."""
        self.raw = json.dumps(
            {
                "message": message,
                "trades": trades or [],
                "watchlist_changes": watchlist_changes or [],
            }
        )

    def replies_raw(self, raw: str) -> None:
        """Answer with exactly this text, valid or not."""
        self.raw = raw

    def fails(self, error: Exception) -> None:
        """Fail the way an unreachable provider does."""
        self.error = error

    def __call__(self, messages):
        self.calls += 1
        self.messages = messages
        if self.error is not None:
            raise self.error
        return self.raw


@pytest.fixture
def stub_model(monkeypatch):
    model = StubModel()
    monkeypatch.setattr("app.chat.complete", model)
    return model


def stored_messages(user_id: str = "default") -> list[tuple[str, str, str | None]]:
    """Chat rows as (role, content, actions JSON), in written order.

    Reads the table rather than the response, so a test can catch a handler
    that reported an exchange it never persisted.
    """
    from app.db import connect

    with connect() as conn:
        return [
            (row["role"], row["content"], row["actions"])
            for row in conn.execute(
                "SELECT role, content, actions FROM chat_messages"
                " WHERE user_id = ? ORDER BY created_at, rowid",
                (user_id,),
            )
        ]


class RecordingSource(MarketDataSource):
    """A market source that records what it was told, and prices on demand.

    Models the simulator's behaviour, which is the harder case to get right:
    `add_ticker` makes the ticker priceable immediately, so a test can check
    that the handler returns a live quote with the 201.
    """

    def __init__(self, cache: PriceCache, tickers: list[str] | None = None) -> None:
        self._cache = cache
        self._tickers = list(tickers or [])
        self.added: list[str] = []
        self.removed: list[str] = []
        #: Set to an exception to make `add_ticker` fail, for the rollback test.
        self.add_error: Exception | None = None

    async def start(self, tickers: list[str]) -> None:
        self._tickers = list(tickers)

    async def stop(self) -> None:
        pass

    async def add_ticker(self, ticker: str) -> None:
        if self.add_error is not None:
            raise self.add_error
        self.added.append(ticker)
        self._tickers.append(ticker)
        self._cache.update(ticker, 50.0)

    async def remove_ticker(self, ticker: str) -> None:
        self.removed.append(ticker)
        self._tickers = [t for t in self._tickers if t != ticker]
        self._cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)


class StubStreamRequest:
    """A `Request` stand-in that disconnects after `ticks` loop iterations.

    The SSE generator is infinite by design, so a test needs a request that
    eventually reports itself gone. `on_tick` fires before each check, which is
    how a test mutates the cache part-way through the stream.
    """

    def __init__(self, ticks: int, on_tick=None) -> None:
        self._ticks = ticks
        self._calls = 0
        self._on_tick = on_tick
        self.client = None  # exercises the "unknown" client-ip branch

    async def is_disconnected(self) -> bool:
        if self._on_tick:
            self._on_tick(self._calls)
        self._calls += 1
        return self._calls > self._ticks


async def collect_sse_frames(cache, ticks: int = 3, on_tick=None, **kwargs) -> list[str]:
    """Run the SSE generator to completion and return the raw frames."""
    from app.market.stream import _generate_events

    request = StubStreamRequest(ticks, on_tick=on_tick)
    return [frame async for frame in _generate_events(cache, request, interval=0, **kwargs)]


def sse_data_frames(frames: list[str]) -> list[dict]:
    """Parse the payloads of default (unnamed) data frames."""
    return [
        json.loads(frame.split("data: ", 1)[1]) for frame in frames if frame.startswith("data: ")
    ]


def snapshot_count() -> int:
    """How many P&L points have been recorded. Two modules assert on this; one
    copy, so a `user_id` filter cannot be added to one and not the other."""
    from app.db import connect

    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM portfolio_snapshots").fetchone()[0]


def snapshot_values() -> list[float]:
    """Recorded totals, in insertion order."""
    from app.db import connect

    with connect() as conn:
        return [row[0] for row in conn.execute("SELECT total_value FROM portfolio_snapshots")]


@pytest.fixture
def price_cache():
    """A cache holding fixed, round prices.

    Deliberately not a running simulator. Money assertions have to be exact —
    "cash went from 10,000 to 8,000" — and a price that moves between the fill
    and the assertion turns every one of them into an approximation.
    """
    from app.market import PriceCache

    cache = PriceCache()
    cache.update("AAPL", 200.0)
    cache.update("GOOGL", 100.0)
    cache.update("MSFT", 400.0)
    return cache


@pytest.fixture
def read_cash():
    """The stored cash balance, read straight from SQLite.

    Reads the table rather than the API response, so a test can catch a handler
    that reports a balance it never persisted.
    """
    from app.db import DEFAULT_USER_ID, connect, get_cash_balance

    def _read(user_id: str = DEFAULT_USER_ID) -> float:
        with connect() as conn:
            return get_cash_balance(conn, user_id)

    return _read


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
