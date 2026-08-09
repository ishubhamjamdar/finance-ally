"""Tests for the shared dependency providers.

Every other API test overrides these, which is what they exist to allow — so
they need their own coverage, or the real providers ship exercised only by
their stand-ins.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.deps import get_market_source, get_price_cache
from app.main import create_app
from app.market import PriceCache


class StubRequest:
    def __init__(self, app):
        self.app = app


class TestGetPriceCache:
    def test_returns_the_apps_cache(self):
        app = create_app()
        assert get_price_cache(StubRequest(app)) is app.state.price_cache

    def test_the_real_provider_serves_a_request(self):
        """No dependency override: the handler reads the app's own cache, and
        a portfolio priced from a cache nothing writes is still a valid
        answer — all cash, no positions."""
        app = create_app()
        app.state.price_cache.update("AAPL", 123.45)

        body = TestClient(app).get("/api/portfolio").json()
        assert body["cash_balance"] == 10000.0


class TestGetMarketSource:
    def test_returns_the_running_source(self):
        app = create_app()
        app.state.market_source = object()
        assert get_market_source(StubRequest(app)) is app.state.market_source

    def test_raises_503_when_there_is_no_source(self):
        """`None` before startup, after shutdown, and after a failover that
        could not start a replacement."""
        with pytest.raises(HTTPException) as excinfo:
            get_market_source(StubRequest(create_app()))

        assert excinfo.value.status_code == 503

    def test_reads_state_per_request_so_failover_is_visible(self):
        """Captured at import, the provider would go on handing out the source
        that died — the whole reason it takes the request."""
        app = create_app()
        first, second = PriceCache(), PriceCache()

        app.state.price_cache = first
        assert get_price_cache(StubRequest(app)) is first
        app.state.price_cache = second
        assert get_price_cache(StubRequest(app)) is second
