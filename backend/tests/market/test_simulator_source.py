"""Integration tests for SimulatorDataSource."""

import asyncio

import pytest

from app.market.cache import PriceCache
from app.market.events import EventLog
from app.market.simulator import SimulatorDataSource


@pytest.mark.asyncio
class TestSimulatorDataSource:
    """Integration tests for the SimulatorDataSource."""

    async def test_start_populates_cache(self):
        """Test that start() immediately populates the cache."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL", "GOOGL"])

        # Cache should have seed prices immediately (before first loop tick)
        assert cache.get("AAPL") is not None
        assert cache.get("GOOGL") is not None

        await source.stop()

    async def test_prices_update_over_time(self):
        """Test that prices are updated periodically."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.05)
        await source.start(["AAPL"])

        initial_version = cache.version
        await asyncio.sleep(0.3)  # Several update cycles

        # Version should have incremented (prices updated)
        assert cache.version > initial_version

        await source.stop()

    async def test_stop_is_clean(self):
        """Test that stop() is clean and idempotent."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL"])
        await source.stop()
        # Double stop should not raise
        await source.stop()

    async def test_add_ticker(self):
        """Test adding a ticker dynamically."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL"])

        await source.add_ticker("TSLA")
        assert "TSLA" in source.get_tickers()
        assert cache.get("TSLA") is not None

        await source.stop()

    async def test_remove_ticker(self):
        """Test removing a ticker."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL", "TSLA"])

        await source.remove_ticker("TSLA")
        assert "TSLA" not in source.get_tickers()
        assert cache.get("TSLA") is None

        await source.stop()

    async def test_get_tickers(self):
        """Test getting the list of active tickers."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL", "GOOGL"])

        tickers = source.get_tickers()
        assert set(tickers) == {"AAPL", "GOOGL"}

        await source.stop()

    async def test_empty_start(self):
        """Test starting with no tickers."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start([])

        assert len(cache) == 0
        assert source.get_tickers() == []

        await source.stop()

    async def test_exception_resilience(self):
        """Test that simulator continues running after errors."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.05)

        # Start with a valid ticker
        await source.start(["AAPL"])

        # Wait for some updates
        await asyncio.sleep(0.15)

        # Task should still be running
        assert source._task is not None
        assert not source._task.done()

        await source.stop()

    async def test_custom_update_interval(self):
        """Test using a custom update interval."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.01)
        await source.start(["AAPL"])

        initial_version = cache.version
        await asyncio.sleep(0.05)  # Should get ~5 updates

        # Should have multiple updates with fast interval
        assert cache.version > initial_version + 2

        await source.stop()

    async def test_custom_event_probability(self):
        """Test creating source with custom event probability."""
        cache = PriceCache()
        # Very high event probability for testing
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1, event_probability=1.0)
        await source.start(["AAPL"])

        # Just verify it starts and stops cleanly
        await asyncio.sleep(0.2)
        await source.stop()


@pytest.mark.asyncio
class TestUnstartedSource:
    """Every mutator must be safe before start() — the lifespan can tear down
    a source that never came up."""

    async def test_add_ticker_before_start_is_a_noop(self):
        source = SimulatorDataSource(price_cache=PriceCache())
        await source.add_ticker("AAPL")
        assert source.get_tickers() == []

    async def test_remove_ticker_before_start_is_a_noop(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        source = SimulatorDataSource(price_cache=cache)
        await source.remove_ticker("AAPL")
        assert cache.get("AAPL") is None  # still clears the cache entry

    async def test_write_without_a_simulator_is_a_noop(self):
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache)
        source._write("AAPL")
        assert len(cache) == 0

    async def test_write_of_an_unknown_ticker_is_a_noop(self):
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache)
        await source.start(["AAPL"])
        source._write("NOSUCH")
        assert cache.get("NOSUCH") is None
        await source.stop()


@pytest.mark.asyncio
class TestEventPublication:
    async def test_shocks_reach_the_event_log(self):
        cache = PriceCache()
        log = EventLog()
        source = SimulatorDataSource(
            price_cache=cache, update_interval=0.01, event_probability=1.0, event_log=log
        )
        await source.start(["AAPL"])
        await asyncio.sleep(0.05)
        await source.stop()

        assert len(log) > 0
        _, events = log.since(0)
        assert events[0].ticker == "AAPL"

    async def test_loop_survives_a_failing_step(self):
        """The try/except sits inside the while: one bad step must not end all
        price updates for the lifetime of the process."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.01)
        await source.start(["AAPL"])

        calls = {"n": 0}
        real_step = source._sim.step

        def flaky():
            calls["n"] += 1
            if calls["n"] <= 2:
                raise RuntimeError("boom")
            return real_step()

        source._sim.step = flaky
        await asyncio.sleep(0.1)
        await source.stop()

        assert calls["n"] > 3  # kept stepping after the failures
