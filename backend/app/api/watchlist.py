"""Watchlist endpoints — PLAN.md §8.

    GET    /api/watchlist           watched tickers with their latest quotes
    POST   /api/watchlist           add a ticker
    DELETE /api/watchlist/{ticker}  remove a ticker

The rules live in `app.watchlist`; these handlers translate between HTTP and
that module and do nothing else. The mutations are `async def` because the
domain functions await the market source — `GET` is a plain `def`, so FastAPI
runs its blocking query in a worker thread.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

from app.api.deps import get_market_source, get_price_cache
from app.api.schemas import TICKER_PATTERN, WatchlistAddRequest
from app.db import WatchlistEntry, connect, list_watchlist
from app.market import MarketDataSource, PriceCache, PriceUpdate, normalize_ticker
from app.watchlist import (
    MarketSourceUnavailableError,
    TickerAlreadyWatchedError,
    TickerNotWatchedError,
    WatchlistError,
)
from app.watchlist import add as add_ticker
from app.watchlist import remove as remove_ticker

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])

TickerPath = Annotated[str, Path(pattern=TICKER_PATTERN)]

#: Which HTTP code each domain failure earns. Listed once, so a new
#: `WatchlistError` that nobody maps degrades to a 400 rather than a 500.
_STATUS_FOR = {
    TickerAlreadyWatchedError: status.HTTP_409_CONFLICT,
    TickerNotWatchedError: status.HTTP_404_NOT_FOUND,
    MarketSourceUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
}


def _http_error(exc: WatchlistError) -> HTTPException:
    return HTTPException(_STATUS_FOR.get(type(exc), status.HTTP_400_BAD_REQUEST), str(exc))


def _row(entry: WatchlistEntry, quote: PriceUpdate | None) -> dict:
    """The watchlist row shape, shared by GET and POST.

    `GET /api/watchlist` and `POST /api/watchlist` return the same object by
    contract; building it in one place is what keeps that true when a field is
    added.

    `quote` is null for a ticker the source has not priced yet — normal for the
    first poll interval after an add on Massive. The frontend renders an em
    dash; it must not substitute zero.
    """
    return {
        "ticker": entry.ticker,
        "added_at": entry.added_at,
        "quote": quote.to_dict() if quote else None,
    }


@router.get("")
def read_watchlist(price_cache: Annotated[PriceCache, Depends(get_price_cache)]) -> dict:
    """Watched tickers, each with its latest quote.

    In add order, not price or alphabetical order, so the grid does not
    reshuffle itself under the user twice a second.
    """
    with connect() as conn:
        entries = list_watchlist(conn)

    prices = price_cache.get_all()
    return {"tickers": [_row(entry, prices.get(entry.ticker)) for entry in entries]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_watchlist_entry(
    payload: WatchlistAddRequest,
    source: Annotated[MarketDataSource, Depends(get_market_source)],
    price_cache: Annotated[PriceCache, Depends(get_price_cache)],
) -> dict:
    """Add a ticker to the watchlist and start streaming it. 409 if already there."""
    try:
        entry = await add_ticker(source, payload.ticker)
    except WatchlistError as exc:
        raise _http_error(exc) from exc

    # Present already for the simulator, which seeds synchronously; null until
    # the next poll on Massive.
    return _row(entry, price_cache.get(entry.ticker))


@router.delete("/{ticker}")
async def remove_watchlist_entry(
    ticker: TickerPath,
    source: Annotated[MarketDataSource, Depends(get_market_source)],
) -> dict:
    """Remove a ticker from the watchlist. 404 if it was not on it.

    `still_tracked` reports whether the market source kept streaming it, which
    it does for a ticker still held as a position.
    """
    try:
        still_tracked = await remove_ticker(source, ticker)
    except WatchlistError as exc:
        raise _http_error(exc) from exc

    return {"ticker": normalize_ticker(ticker), "removed": True, "still_tracked": still_tracked}
