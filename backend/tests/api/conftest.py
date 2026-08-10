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
from tests.conftest import PLAN_DEFAULT_WATCHLIST, RecordingSource


@pytest.fixture
def source(price_cache):
    """A source already tracking the seeded watchlist.

    It has to start in sync with the database, because `app.watchlist.reconcile`
    makes the source's subscriptions equal `watchlist ∪ positions` — against an
    out-of-sync source every test would record the seven catch-up adds as well
    as the change it was actually making.
    """
    return RecordingSource(price_cache, tickers=list(PLAN_DEFAULT_WATCHLIST))


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
