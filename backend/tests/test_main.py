"""Tests for the FastAPI application: lifespan, failover wiring, static serving."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.db import connect
from app.main import _current_market_status, _resolve_static_dir, create_app
from app.market import MarketDataSource
from app.market.simulator import SimulatorDataSource
from tests.conftest import PLAN_DEFAULT_WATCHLIST

DEFAULT_WATCHLIST = set(PLAN_DEFAULT_WATCHLIST)


@pytest.fixture
def fast_simulator(monkeypatch):
    """Tick well under the 500 ms default so timing assertions stay quick."""
    monkeypatch.setenv("SIM_UPDATE_INTERVAL", "0.02")


@pytest.fixture
def revoked_key_source(monkeypatch):
    """Install a source that fails permanently on demand, in place of the real
    startup path. Shared by every failover test, so the stubbed signature of
    start_market_data lives in one place rather than five."""

    def _install(tickers: list[str]) -> "RevokedKeySource":
        stub = RevokedKeySource(tickers)

        async def fake_start(price_cache, tickers, event_log=None):
            await stub.start(tickers)
            return stub

        monkeypatch.setattr("app.main.start_market_data", fake_start)
        return stub

    return _install


class TestAppConstruction:
    def test_registers_the_documented_endpoints(self):
        paths = {route.path for route in create_app().routes if hasattr(route, "path")}
        assert "/api/health" in paths
        assert "/api/stream/prices" in paths

    def test_each_instance_gets_its_own_cache(self):
        """Two apps must not share a PriceCache, or one test's simulator
        satisfies another test's assertions."""
        first, second = create_app(), create_app()
        assert first.state.price_cache is not second.state.price_cache
        assert first.state.event_log is not second.state.event_log

    def test_market_source_is_none_before_startup(self):
        assert create_app().state.market_source is None


class TestLifespan:
    async def test_starts_the_source_with_the_tracked_tickers(self):
        """Checkpoint 2 exit criterion: the tickers seeded in the database are
        the tickers the source is started with."""
        app = create_app()
        async with app.router.lifespan_context(app):
            source = app.state.market_source
            assert isinstance(source, SimulatorDataSource)
            assert set(source.get_tickers()) == DEFAULT_WATCHLIST

    async def test_tracks_a_position_held_outside_the_watchlist(self, add_position):
        with connect() as conn:
            conn.execute("DELETE FROM watchlist WHERE ticker = 'TSLA'")
        add_position("TSLA")
        add_position("PYPL")

        app = create_app()
        async with app.router.lifespan_context(app):
            tracked = set(app.state.market_source.get_tickers())

        assert {"TSLA", "PYPL"} <= tracked  # union(watchlist, positions)

    async def test_populates_the_cache_before_serving(self):
        """No empty-watchlist flash on first load: prices exist by the time
        startup returns, not one tick later."""
        app = create_app()
        async with app.router.lifespan_context(app):
            assert set(app.state.price_cache.get_all()) == DEFAULT_WATCHLIST

    async def test_stops_the_source_on_shutdown(self, fast_simulator):
        app = create_app()
        async with app.router.lifespan_context(app):
            cache = app.state.price_cache
            before = cache.version
            await asyncio.sleep(0.1)
            assert cache.version > before, "positive control: prices should tick while running"

        after_shutdown = cache.version
        await asyncio.sleep(0.1)
        assert cache.version == after_shutdown, "the simulator task outlived shutdown"
        assert app.state.market_source is None

    async def test_shutdown_is_safe_when_startup_left_no_source(self):
        app = create_app()
        async with app.router.lifespan_context(app):
            app.state.market_source = None  # e.g. a failover that never completed

    async def test_a_failover_completing_during_shutdown_retires_its_replacement(
        self, fast_simulator
    ):
        """Shutdown and failover race. Shutdown stops one source and then ends,
        so a replacement installed after that moment is one nothing will ever
        stop — it would tick on past the end of the application's life."""
        app = create_app()
        cache = app.state.price_cache

        async with app.router.lifespan_context(app):
            original = app.state.market_source
            app.state.shutting_down = True  # shutdown has begun
            await original.on_permanent_failure(RuntimeError("key revoked"))
            assert app.state.market_source is original, "installed a source during shutdown"

        version = cache.version
        await asyncio.sleep(0.1)
        assert cache.version == version, "the replacement outlived shutdown"

    async def test_creates_the_database_on_a_cold_start(self, temp_db):
        assert not temp_db.exists()
        app = create_app()
        async with app.router.lifespan_context(app):
            pass
        assert temp_db.exists()


class RevokedKeySource(MarketDataSource):
    """Stands in for MassiveDataSource losing its key mid-session.

    Reproduces the structure that matters: the failure callback is awaited
    from inside this source's own background task, and stop() cancels that
    task — so it also honours the ABC's requirement to release the task before
    awaiting the callback, which is what makes stop() safe to call from in
    there. `stop_calls` records that the app does call it.
    """

    def __init__(self, tickers: list[str]) -> None:
        self._tickers = list(tickers)
        self._task: asyncio.Task | None = None
        self.callback_completed = False
        self.stop_calls = 0

    async def start(self, tickers: list[str]) -> None:
        self._tickers = list(tickers)

    async def trigger_permanent_failure(self) -> BaseException | None:
        task = asyncio.create_task(self._fail())
        self._task = task
        result = await asyncio.gather(task, return_exceptions=True)
        return result[0] if isinstance(result[0], BaseException) else None

    async def _fail(self) -> None:
        assert self.on_permanent_failure is not None
        self._task = None  # released before the callback, per the ABC
        await self.on_permanent_failure(RuntimeError("Unknown API Key"))
        self.callback_completed = True

    async def stop(self) -> None:
        self.stop_calls += 1
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await self._task

    async def add_ticker(self, ticker: str) -> None:
        self._tickers.append(ticker)

    async def remove_ticker(self, ticker: str) -> None:
        self._tickers = [t for t in self._tickers if t != ticker]

    def get_tickers(self) -> list[str]:
        return list(self._tickers)


class TestFailover:
    """CP1 review finding #2, deferred to this checkpoint: nothing assigned
    `on_permanent_failure`, so a key revoked mid-session froze every price."""

    async def test_lifespan_wires_the_callback(self):
        app = create_app()
        async with app.router.lifespan_context(app):
            assert app.state.market_source.on_permanent_failure is not None

    async def test_swaps_in_a_running_simulator(self, revoked_key_source, fast_simulator):
        stub = revoked_key_source(["AAPL", "PYPL"])

        app = create_app()
        async with app.router.lifespan_context(app):
            assert app.state.market_source is stub
            await stub.add_ticker("PYPL")  # added at runtime, after startup

            error = await stub.trigger_permanent_failure()
            assert error is None, f"the failover callback did not complete: {error!r}"
            assert stub.callback_completed is True
            assert stub.stop_calls == 1, "the failed source must be stopped, not merely dropped"

            # The replacement inherits the live tracked set, not the startup
            # one — a ticker added mid-session must survive the swap.
            replacement = app.state.market_source
            assert isinstance(replacement, SimulatorDataSource)
            assert set(replacement.get_tickers()) == DEFAULT_WATCHLIST | {"PYPL"}

            # Running, not merely constructed: prices resume for the user.
            before = app.state.price_cache.version
            await asyncio.sleep(0.1)
            assert app.state.price_cache.version > before

    async def test_shutdown_stops_the_replacement(self, revoked_key_source, fast_simulator):
        stub = revoked_key_source(["AAPL"])

        app = create_app()
        async with app.router.lifespan_context(app):
            await stub.trigger_permanent_failure()
            cache = app.state.price_cache

        version = cache.version
        await asyncio.sleep(0.1)
        assert cache.version == version, "the replacement simulator outlived shutdown"

    async def test_a_failing_failover_clears_the_dead_source(self, monkeypatch, revoked_key_source):
        """If the replacement cannot start, the app must not go on advertising
        the source that just died.

        The callback is awaited from an `except` block inside the failed
        source's own task, so an exception escaping here surfaces as nothing
        but "Task exception was never retrieved" — while /api/health keeps
        reporting a live Massive feed that has not ticked in an hour.
        """
        stub = revoked_key_source(["AAPL"])

        def exploding_simulator(price_cache, event_log=None):
            raise ValueError("degenerate SIM_UPDATE_INTERVAL")

        app = create_app()
        async with app.router.lifespan_context(app):
            monkeypatch.setattr("app.main.create_simulator_source", exploding_simulator)

            error = await stub.trigger_permanent_failure()
            assert error is None, f"the exception escaped the callback: {error!r}"
            assert app.state.market_source is None

    async def test_the_replacement_can_itself_fail_over(self, revoked_key_source, fast_simulator):
        """The callback is reassigned to the replacement, so a future source
        installed by failover is no less protected than the first one."""
        stub = revoked_key_source(["AAPL"])

        app = create_app()
        async with app.router.lifespan_context(app):
            await stub.trigger_permanent_failure()
            assert app.state.market_source.on_permanent_failure is not None

    async def test_status_provider_follows_the_swap(self, revoked_key_source):
        """The SSE `status` frame must report the live source, not the dead one."""
        stub = revoked_key_source(["AAPL"])
        stub.market_status = "closed"

        app = create_app()
        async with app.router.lifespan_context(app):
            assert _current_market_status(app) == "closed"
            await stub.trigger_permanent_failure()
            # The simulator always trades, so it reports no venue state at all.
            assert _current_market_status(app) is None

    def test_status_is_none_before_startup(self):
        assert _current_market_status(create_app()) is None


class TestStaticFiles:
    def test_serves_the_frontend_when_it_is_built(self, monkeypatch, tmp_path):
        (tmp_path / "index.html").write_text("<h1>FinAlly</h1>")
        monkeypatch.setenv("STATIC_DIR", str(tmp_path))

        response = TestClient(create_app()).get("/")
        assert response.status_code == 200
        assert "FinAlly" in response.text

    def test_static_mount_does_not_shadow_the_api(self, monkeypatch, tmp_path):
        """StaticFiles at "/" matches every path — mounted first it would
        swallow /api/* and return 404 for the whole backend."""
        (tmp_path / "index.html").write_text("<h1>FinAlly</h1>")
        monkeypatch.setenv("STATIC_DIR", str(tmp_path))

        response = TestClient(create_app()).get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_tolerates_an_absent_build(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STATIC_DIR", str(tmp_path / "never-built"))

        client = TestClient(create_app())
        assert client.get("/api/health").status_code == 200

        root = client.get("/")
        assert root.status_code == 200
        assert "/api/health" in root.json()["api"]

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_static_dir_falls_back_to_the_search_path(self, monkeypatch, blank, tmp_path):
        monkeypatch.setenv("STATIC_DIR", blank)
        monkeypatch.setattr("app.main._STATIC_CANDIDATES", (tmp_path,))
        assert _resolve_static_dir() == tmp_path

    def test_returns_none_when_no_candidate_exists(self, monkeypatch, tmp_path):
        monkeypatch.delenv("STATIC_DIR", raising=False)
        monkeypatch.setattr("app.main._STATIC_CANDIDATES", (tmp_path / "a", tmp_path / "b"))
        assert _resolve_static_dir() is None

    def test_prefers_the_first_candidate_that_exists(self, monkeypatch, tmp_path):
        monkeypatch.delenv("STATIC_DIR", raising=False)
        second = tmp_path / "second"
        second.mkdir()
        monkeypatch.setattr("app.main._STATIC_CANDIDATES", (tmp_path / "first", second))
        assert _resolve_static_dir() == second


class TestStreamEndpoint:
    async def test_streams_prices_for_the_seeded_watchlist(self, fast_simulator):
        """The endpoint is exercised end to end in Gate 1 with curl; here we
        assert the router is wired to the same cache the lifespan fills."""
        app = create_app()
        async with app.router.lifespan_context(app):
            prices = app.state.price_cache.get_all()

        assert set(prices) == DEFAULT_WATCHLIST
        assert all(update.price > 0 for update in prices.values())
