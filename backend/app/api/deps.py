"""Shared FastAPI dependencies — MARKET_DATA_DESIGN.md §13.1.

Both providers read `request.app.state` per request rather than closing over
the objects at import. `market_source` is replaced by failover mid-session, and
a captured reference would go on serving the dead source forever.

Injecting them (rather than reaching into `app.state` in each handler) also
makes `app.dependency_overrides` available, which is how trade tests point a
handler at a stub cache without starting a simulator.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

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


def require_live_market(
    source: Annotated[MarketDataSource, Depends(get_market_source)],
) -> None:
    """Refuse the request unless a market data source is running.

    The same 503 as `get_market_source`, for endpoints that need the *policy*
    rather than the object. `POST /api/portfolio/trade` fills from the cache and
    never touches the source, but must not fill at all once the feed has
    stopped: after a failover that could not start a replacement, every price is
    frozen at its last value and would stay fillable for the life of the
    process, while the watchlist endpoints next door returned 503.

    Declaring the policy by name beats injecting an object the handler ignores.
    Checkpoint 4's chat endpoint executes trades too, and should use this.

    Resolves `get_market_source` through `Depends`, not by calling it, so that
    an `app.dependency_overrides` entry for it reaches this policy too — a
    direct call would bypass the override and read `app.state` behind the
    test's back.
    """
    del source
