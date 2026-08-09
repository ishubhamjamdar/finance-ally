"""Tests for market data source factory and startup failover."""

import os
from unittest.mock import patch

import pytest

from app.market.cache import PriceCache
from app.market.factory import create_market_data_source, start_market_data
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource

from .conftest import (
    NOT_AUTHORIZED_BODY,
    UNKNOWN_KEY_BODY,
    make_snapshot,
    offline_massive,
)


class TestFactory:
    """Tests for create_market_data_source factory."""

    def test_creates_simulator_when_no_api_key(self):
        """Test that simulator is created when MASSIVE_API_KEY is not set."""
        cache = PriceCache()

        with patch.dict(os.environ, {}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, SimulatorDataSource)

    def test_creates_simulator_when_api_key_empty(self):
        """Test that simulator is created when MASSIVE_API_KEY is empty."""
        cache = PriceCache()

        with patch.dict(os.environ, {"MASSIVE_API_KEY": ""}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, SimulatorDataSource)

    def test_creates_simulator_when_api_key_whitespace(self):
        """Test that simulator is created when MASSIVE_API_KEY is whitespace."""
        cache = PriceCache()

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "   "}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, SimulatorDataSource)

    def test_creates_massive_when_api_key_set(self):
        """Test that Massive client is created when MASSIVE_API_KEY is set."""
        cache = PriceCache()

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "test-key"}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, MassiveDataSource)

    def test_massive_receives_api_key(self):
        """Test that Massive client receives the API key."""
        cache = PriceCache()

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "test-key-123"}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, MassiveDataSource)
        assert source._api_key == "test-key-123"

    def test_simulator_receives_cache(self):
        """Test that simulator receives the cache reference."""
        cache = PriceCache()

        with patch.dict(os.environ, {}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, SimulatorDataSource)
        assert source._cache is cache

    def test_massive_receives_cache(self):
        """Test that Massive client receives the cache reference."""
        cache = PriceCache()

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "test-key"}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, MassiveDataSource)
        assert source._cache is cache


class TestEnvironmentTuning:
    """The factory is the only module that reads the environment."""

    def test_massive_poll_interval_from_env(self):
        with patch.dict(
            os.environ, {"MASSIVE_API_KEY": "k", "MASSIVE_POLL_INTERVAL": "3"}, clear=True
        ):
            source = create_market_data_source(PriceCache())
        assert source._interval == 3.0

    def test_massive_poll_interval_defaults_to_15(self):
        with patch.dict(os.environ, {"MASSIVE_API_KEY": "k"}, clear=True):
            source = create_market_data_source(PriceCache())
        assert source._interval == 15.0

    def test_simulator_interval_and_event_probability_from_env(self):
        with patch.dict(
            os.environ,
            {"SIM_UPDATE_INTERVAL": "0.25", "SIM_EVENT_PROBABILITY": "0.5"},
            clear=True,
        ):
            source = create_market_data_source(PriceCache())
        assert source._interval == 0.25
        assert source._event_prob == 0.5

    def test_simulator_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            source = create_market_data_source(PriceCache())
        assert source._interval == 0.5
        assert source._event_prob == 2e-5

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_numeric_env_falls_back_to_default(self, blank):
        """`.env` files routinely carry `MASSIVE_POLL_INTERVAL=` with no value.
        float("") raises, which would take startup down over a tuning knob."""
        with patch.dict(
            os.environ, {"MASSIVE_API_KEY": "k", "MASSIVE_POLL_INTERVAL": blank}, clear=True
        ):
            source = create_market_data_source(PriceCache())
        assert source._interval == 15.0

    def test_malformed_numeric_env_falls_back_to_default(self):
        with patch.dict(
            os.environ, {"SIM_UPDATE_INTERVAL": "fast", "SIM_EVENT_PROBABILITY": "?"}, clear=True
        ):
            source = create_market_data_source(PriceCache())
        assert source._interval == 0.5
        assert source._event_prob == 2e-5

    def test_event_log_is_passed_through(self):
        from app.market.events import EventLog

        log = EventLog()
        with patch.dict(os.environ, {}, clear=True):
            source = create_market_data_source(PriceCache(), event_log=log)
        assert source._event_log is log


@pytest.mark.asyncio
class TestStartMarketData:
    """Verify by outcome, not configuration: a valid free-tier key that returns
    no data must degrade to a working simulator, not a permanently empty UI."""

    async def test_starts_simulator_when_no_key(self):
        cache = PriceCache()
        with patch.dict(os.environ, {}, clear=True):
            source = await start_market_data(cache, ["AAPL", "GOOGL"])

        assert isinstance(source, SimulatorDataSource)
        assert cache.get_price("AAPL") is not None
        await source.stop()

    async def test_simulator_failure_propagates_rather_than_recursing(self):
        """The simulator IS the fallback — when it is what failed there is
        nothing left to swap in, so the error must surface."""
        cache = PriceCache()
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(
                SimulatorDataSource, "start", side_effect=RuntimeError("simulator broken")
            ):
                with pytest.raises(RuntimeError, match="simulator broken"):
                    await start_market_data(cache, ["AAPL"])

    async def test_uses_massive_when_it_works(self):
        cache = PriceCache()
        with patch.dict(os.environ, {"MASSIVE_API_KEY": "k"}, clear=True):
            with offline_massive(snapshots=[make_snapshot("AAPL")]):
                source = await start_market_data(cache, ["AAPL"])

        assert isinstance(source, MassiveDataSource)
        assert cache.get_price("AAPL") == 190.5
        await source.stop()

    async def test_falls_back_on_permanent_failure(self):
        """An invalid key must yield a running simulator, not an empty cache."""
        cache = PriceCache()
        with patch.dict(os.environ, {"MASSIVE_API_KEY": "bad"}, clear=True):
            with offline_massive(error=UNKNOWN_KEY_BODY):
                source = await start_market_data(cache, ["AAPL"])

        assert isinstance(source, SimulatorDataSource)
        assert cache.get_price("AAPL") is not None
        await source.stop()

    async def test_falls_back_when_authenticated_but_no_prices(self):
        """The worst failure mode: a valid Basic-plan key, no snapshot
        entitlement, no error anywhere — indistinguishable from a healthy app."""
        cache = PriceCache()
        with patch.dict(os.environ, {"MASSIVE_API_KEY": "basic"}, clear=True):
            with offline_massive(snapshots=[]):
                source = await start_market_data(cache, ["AAPL"])

        assert isinstance(source, SimulatorDataSource)
        assert cache.get_price("AAPL") is not None
        await source.stop()

    async def test_falls_back_when_none_of_the_requested_tickers_priced(self):
        """A pre-populated cache must not vouch for a source that priced nothing
        the caller asked for."""
        cache = PriceCache()
        cache.update("STALE", 1.0)  # left over from an earlier run / shared cache

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "k"}, clear=True):
            with offline_massive(snapshots=[]):
                source = await start_market_data(cache, ["AAPL"])

        assert isinstance(source, SimulatorDataSource)
        assert cache.get_price("AAPL") is not None
        await source.stop()

    async def test_partial_coverage_keeps_massive_but_warns(self, caplog):
        """One delisted ticker should not force everyone onto the simulator —
        but the gap must be visible."""
        cache = PriceCache()
        with patch.dict(os.environ, {"MASSIVE_API_KEY": "k"}, clear=True):
            with offline_massive(snapshots=[make_snapshot("AAPL")]):
                with caplog.at_level("WARNING"):
                    source = await start_market_data(cache, ["AAPL", "NOSUCH"])

        assert isinstance(source, MassiveDataSource)
        assert "NOSUCH" in caplog.text
        await source.stop()

    async def test_fallback_simulator_honours_env_tuning(self):
        """A user who sets SIM_UPDATE_INTERVAL should not silently get stock
        defaults the moment their API key stops working."""
        cache = PriceCache()
        env = {"MASSIVE_API_KEY": "bad", "SIM_UPDATE_INTERVAL": "0.05"}
        with patch.dict(os.environ, env, clear=True):
            with offline_massive(error=UNKNOWN_KEY_BODY):
                source = await start_market_data(cache, ["AAPL"])

        assert isinstance(source, SimulatorDataSource)
        assert source._interval == 0.05
        await source.stop()

    async def test_fallback_receives_event_log(self):
        from app.market.events import EventLog

        cache = PriceCache()
        log = EventLog()
        with patch.dict(os.environ, {"MASSIVE_API_KEY": "bad"}, clear=True):
            with offline_massive(error=NOT_AUTHORIZED_BODY):
                source = await start_market_data(cache, ["AAPL"], event_log=log)

        assert source._event_log is log
        await source.stop()
