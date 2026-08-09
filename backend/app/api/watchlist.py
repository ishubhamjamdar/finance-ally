"""Watchlist endpoints — PLAN.md §8.

    GET    /api/watchlist           watched tickers with their latest quotes
    POST   /api/watchlist           add a ticker
    DELETE /api/watchlist/{ticker}  remove a ticker

The mutations are `async def` with the SQLite work pushed to a thread, because
each of them has to do two things that cannot be split: write the row *and*
tell the live market source. A plain `def` handler threads the query
automatically but cannot `await source.add_ticker()`, and a watchlist row whose
ticker the source never heard about is a row that never gets a price
(MARKET_DATA_DESIGN.md §13.4).
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi.concurrency import run_in_threadpool

from app.api.deps import get_market_source, get_price_cache
from app.api.schemas import TICKER_PATTERN, WatchlistAddRequest
from app.db import (
    WatchlistEntry,
    add_watchlist_entry,
    connect,
    delete_watchlist_entry,
    get_position,
    list_watchlist,
    transaction,
)
from app.market import MarketDataSource, PriceCache, normalize_ticker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])

TickerPath = Annotated[str, Path(pattern=TICKER_PATTERN)]


@router.get("")
def read_watchlist(price_cache: Annotated[PriceCache, Depends(get_price_cache)]) -> dict:
    """Watched tickers in the order they were added, each with its latest quote.

    `quote` is null for a ticker the source has not priced yet — normal for the
    first poll interval after an add on Massive. The frontend renders an em
    dash; it must not substitute zero.

    Add order, not price or alphabetical order, so the grid does not reshuffle
    itself under the user twice a second.
    """
    entries = _load_watchlist()
    prices = price_cache.get_all()
    return {
        "tickers": [
            {
                "ticker": entry.ticker,
                "added_at": entry.added_at,
                "quote": prices[entry.ticker].to_dict() if entry.ticker in prices else None,
            }
            for entry in entries
        ]
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_watchlist_entry(
    payload: WatchlistAddRequest,
    source: Annotated[MarketDataSource, Depends(get_market_source)],
    price_cache: Annotated[PriceCache, Depends(get_price_cache)],
) -> dict:
    """Add a ticker to the watchlist and start streaming it.

    409 if it is already there. Database first, source second, one handler.
    """
    ticker = normalize_ticker(payload.ticker)

    entry = await run_in_threadpool(_insert_watchlist_row, ticker)
    if entry is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{ticker} is already on the watchlist.")

    try:
        await source.add_ticker(ticker)
    except Exception as exc:
        # Neither shipped source can fail here — both only mutate a local list
        # — but the row must not outlive a source that rejected it, or the
        # watchlist would show a ticker nothing will ever price.
        logger.exception("Source rejected %s; rolling the watchlist row back", ticker)
        await run_in_threadpool(_delete_watchlist_row, ticker)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Could not start streaming {ticker}.",
        ) from exc

    # Present already for the simulator, which seeds synchronously; null until
    # the next poll on Massive.
    quote = price_cache.get(ticker)
    return {
        "ticker": entry.ticker,
        "added_at": entry.added_at,
        "quote": quote.to_dict() if quote else None,
    }


@router.delete("/{ticker}")
async def remove_watchlist_entry(
    ticker: TickerPath,
    source: Annotated[MarketDataSource, Depends(get_market_source)],
) -> dict:
    """Remove a ticker from the watchlist.

    404 if it was not on it. The position, if any, is untouched — removing a
    ticker from the watchlist is not a sale.

    A ticker still held stays subscribed to the market source. The tracked set
    is `union(watchlist, positions)` — the same rule `load_tracked_tickers()`
    applies at startup — and dropping a held ticker's price would make the
    portfolio total silently lose that position.
    """
    ticker = normalize_ticker(ticker)

    removed, still_held = await run_in_threadpool(_delete_watchlist_row, ticker)
    if not removed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{ticker} is not on the watchlist.")

    if not still_held:
        await source.remove_ticker(ticker)

    return {"ticker": ticker, "removed": True, "still_tracked": still_held}


# --- blocking database work, called through run_in_threadpool ------------


def _load_watchlist() -> list[WatchlistEntry]:
    with connect() as conn:
        return list_watchlist(conn)


def _insert_watchlist_row(ticker: str) -> WatchlistEntry | None:
    with transaction() as conn:
        return add_watchlist_entry(conn, ticker)


def _delete_watchlist_row(ticker: str) -> tuple[bool, bool]:
    """Delete the row and report `(was_present, still_held_as_a_position)`.

    Both answers come from one connection so the caller cannot act on a
    position that was sold between two separate round trips.
    """
    with transaction() as conn:
        removed = delete_watchlist_entry(conn, ticker)
        position = get_position(conn, ticker)
    return removed, position is not None and position.quantity != 0
