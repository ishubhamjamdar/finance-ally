"""Shared FastAPI dependencies — MARKET_DATA_DESIGN.md §13.1.

Both providers read `request.app.state` per request rather than closing over
the objects at import. `market_source` is replaced by failover mid-session, and
a captured reference would go on serving the dead source forever.

Injecting them (rather than reaching into `app.state` in each handler) also
makes `app.dependency_overrides` available, which is how trade tests point a
handler at a stub cache without starting a simulator.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from app.market import MarketDataSource, PriceCache


def get_price_cache(request: Request) -> PriceCache:
    """The live price cache. Always present — `create_app` builds one."""
    return request.app.state.price_cache


def get_market_source(request: Request) -> MarketDataSource:
    """The running market data source.

    `None` before startup, after shutdown, and after a failover that could not
    start a replacement. Handlers that must talk to the source — the watchlist
    mutations — cannot do anything useful in that state, so it is answered once
    here as a 503 rather than re-checked in each of them.

    `/api/health` deliberately does not use this: reporting "no source" is its
    entire job, so it reads `app.state` directly.
    """
    source = request.app.state.market_source
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Market data is not running.",
        )
    return source
