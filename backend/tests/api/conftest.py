"""Fixtures for the REST API tests.

The client is built **without** running the lifespan. That is deliberate: a
lifespan would start a real simulator writing unpredictable prices, and a
30-second snapshot task writing rows the snapshot assertions would then have to
tolerate. Instead the two dependencies the handlers actually use are overridden
with a fixed cache and a recording source, which is what `app/api/deps.py`
exists to make possible.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_market_source, get_price_cache
from app.main import create_app
from app.market import MarketDataSource, PriceCache


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


@pytest.fixture
def source(price_cache):
    return RecordingSource(price_cache, tickers=sorted(price_cache.get_all()))


@pytest.fixture
def app(price_cache, source):
    application = create_app()
    application.dependency_overrides[get_price_cache] = lambda: price_cache
    application.dependency_overrides[get_market_source] = lambda: source
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def sourceless_client(price_cache):
    """A client whose market source is absent, as it is after a failover that
    could not start a replacement."""
    application = create_app()
    application.dependency_overrides[get_price_cache] = lambda: price_cache
    return TestClient(application)
