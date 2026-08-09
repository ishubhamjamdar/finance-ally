"""Contract tests — every MarketDataSource must satisfy these.

The strongest guard against the two implementations drifting apart. A third
provider added later inherits this suite unchanged.
"""

import pytest

from app.market.cache import PriceCache
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource

from .conftest import make_snapshot


@pytest.fixture(autouse=True)
def _no_network():
    """These tests call start(), which builds a real RESTClient and then polls
    market status over the wire. Patch the class, not the instance — stubbing
    `source._client` does nothing because start() overwrites it."""
    from unittest.mock import patch

    def fetch(self):
        return [make_snapshot(t) for t in self._tickers]

    with patch("app.market.massive_client.RESTClient"):
        with patch.object(MassiveDataSource, "_fetch_snapshots", fetch):
            yield


def _make_massive(cache: PriceCache) -> MassiveDataSource:
    """A Massive source that answers from fixtures instead of the network."""
    return MassiveDataSource(api_key="k", price_cache=cache, poll_interval=60.0)


def _make_simulator(cache: PriceCache) -> SimulatorDataSource:
    return SimulatorDataSource(cache, update_interval=0.05)


@pytest.fixture(params=["simulator", "massive"])
def source_factory(request):
    return {"simulator": _make_simulator, "massive": _make_massive}[request.param]


@pytest.mark.asyncio
async def test_start_populates_cache(source_factory):
    """start() must not return until at least one price exists, so the first
    SSE frame and the first trade both have something to work with."""
    cache = PriceCache()
    source = source_factory(cache)
    await source.start(["AAPL", "GOOGL"])
    assert cache.get("AAPL") is not None
    await source.stop()


@pytest.mark.asyncio
async def test_tickers_are_normalised(source_factory):
    cache = PriceCache()
    source = source_factory(cache)
    await source.start([" aapl "])
    assert source.get_tickers() == ["AAPL"]
    await source.stop()


@pytest.mark.asyncio
async def test_stop_is_idempotent_and_safe_before_start(source_factory):
    source = source_factory(PriceCache())
    await source.stop()  # never started
    await source.start(["AAPL"])
    await source.stop()
    await source.stop()


@pytest.mark.asyncio
async def test_remove_drops_from_cache_and_set(source_factory):
    cache = PriceCache()
    source = source_factory(cache)
    await source.start(["AAPL", "GOOGL"])
    await source.remove_ticker("GOOGL")
    assert "GOOGL" not in source.get_tickers()
    assert cache.get("GOOGL") is None
    await source.stop()


@pytest.mark.asyncio
async def test_add_ticker_joins_the_active_set(source_factory):
    cache = PriceCache()
    source = source_factory(cache)
    await source.start(["AAPL"])
    await source.add_ticker("tsla")
    assert "TSLA" in source.get_tickers()
    await source.stop()


@pytest.mark.asyncio
async def test_get_tickers_returns_a_copy(source_factory):
    """Mutating the returned list must not corrupt the source's state — for the
    simulator it would desync the Cholesky factor from the ticker list."""
    cache = PriceCache()
    source = source_factory(cache)
    await source.start(["AAPL", "GOOGL"])
    source.get_tickers().clear()
    assert len(source.get_tickers()) == 2
    await source.stop()


@pytest.mark.asyncio
async def test_remove_unknown_ticker_is_a_noop(source_factory):
    cache = PriceCache()
    source = source_factory(cache)
    await source.start(["AAPL"])
    await source.remove_ticker("NOSUCH")
    assert source.get_tickers() == ["AAPL"]
    await source.stop()
