"""Watchlist operations — the tracked-set rules, in one place.

The counterpart to `app.portfolio`, and it exists for the same reason. PLAN.md
§Checkpoint 4 requires the LLM's `watchlist_changes` to execute "through the
*same* validation path as Checkpoint 3". A rule that lives only inside a
FastAPI handler has no such path: the chat endpoint would have to either
re-implement it or catch an `HTTPException(409)` that FastAPI would then turn
into a 409 on `POST /api/chat`, aborting the whole reply instead of telling the
user that AAPL was already watched.

So the operations live here, raise domain errors, and the handlers in
`app/api/watchlist.py` do nothing but map those errors to status codes.

**The tracked set is `watchlist ∪ positions(quantity != 0)`** — one rule, one
owner. `load_tracked_tickers()` computes it at startup, and `reconcile()`
re-imposes it after every mutation, rather than each handler trying to maintain
it incrementally with its own add/remove/undo reasoning.
"""

from __future__ import annotations

import logging

from fastapi.concurrency import run_in_threadpool

from app.db import (
    DEFAULT_USER_ID,
    WatchlistEntry,
    add_watchlist_entry,
    delete_watchlist_entry,
    load_tracked_tickers,
    transaction,
)
from app.market import MarketDataSource, normalize_ticker

logger = logging.getLogger(__name__)


class WatchlistError(Exception):
    """A watchlist change that could not be made. Carries a message for the user."""


class TickerAlreadyWatchedError(WatchlistError):
    """The ticker is already on the watchlist."""


class TickerNotWatchedError(WatchlistError):
    """The ticker was not on the watchlist, so there was nothing to remove."""


class MarketSourceUnavailableError(WatchlistError):
    """The market source refused the subscription change."""


async def add(
    source: MarketDataSource, ticker: str, user_id: str = DEFAULT_USER_ID
) -> WatchlistEntry:
    """Add a ticker to the watchlist and start streaming it.

    Database first, source second. If the source refuses, the row is removed
    again: a watchlist row whose ticker nothing prices would sit there showing
    an em dash forever, with no way for the user to find out why
    (MARKET_DATA_DESIGN.md §13.4).
    """
    ticker = normalize_ticker(ticker)

    entry = await run_in_threadpool(_insert_row, ticker, user_id)
    if entry is None:
        raise TickerAlreadyWatchedError(f"{ticker} is already on the watchlist.")

    try:
        await reconcile(source, user_id)
    except Exception as exc:
        logger.exception("Source rejected %s; rolling the watchlist row back", ticker)
        await run_in_threadpool(_delete_row, ticker, user_id)
        raise MarketSourceUnavailableError(f"Could not start streaming {ticker}.") from exc

    return entry


async def remove(source: MarketDataSource, ticker: str, user_id: str = DEFAULT_USER_ID) -> bool:
    """Remove a ticker from the watchlist. Returns whether it is still tracked.

    Not a sale: the position, if any, is untouched — and a held ticker stays
    subscribed, because it is still in the tracked set. Dropping its price
    would make the portfolio total silently lose that position.
    """
    ticker = normalize_ticker(ticker)

    if not await run_in_threadpool(_delete_row, ticker, user_id):
        raise TickerNotWatchedError(f"{ticker} is not on the watchlist.")

    try:
        tracked = await reconcile(source, user_id)
    except Exception as exc:
        logger.exception("Source could not drop %s; restoring the watchlist row", ticker)
        await run_in_threadpool(_insert_row, ticker, user_id)
        raise MarketSourceUnavailableError(f"Could not stop streaming {ticker}.") from exc

    return ticker in tracked


async def reconcile(source: MarketDataSource, user_id: str = DEFAULT_USER_ID) -> set[str]:
    """Make the source's subscriptions equal `watchlist ∪ positions`.

    Returns the tracked set. Idempotent, so it is safe to call after any
    mutation, and calling it twice costs two reads and changes nothing.

    Removals run first and the wanted set is then re-read, because the two
    steps cannot share a transaction — one is SQLite and the other an await —
    so a buy can commit between them. Read once and applied blindly, that buy's
    ticker would be evicted a moment after the position opened, leaving a
    holding no source prices: null mark, excluded from the total, for the life
    of the process. Re-reading turns that race into an add.
    """
    wanted = set(await run_in_threadpool(load_tracked_tickers, user_id))
    for ticker in sorted(set(source.get_tickers()) - wanted):
        await source.remove_ticker(ticker)

    wanted = set(await run_in_threadpool(load_tracked_tickers, user_id))
    for ticker in sorted(wanted - set(source.get_tickers())):
        await source.add_ticker(ticker)

    return wanted


# --- blocking database work, called through run_in_threadpool ------------


def _insert_row(ticker: str, user_id: str) -> WatchlistEntry | None:
    with transaction() as conn:
        return add_watchlist_entry(conn, ticker, user_id)


def _delete_row(ticker: str, user_id: str) -> bool:
    with transaction() as conn:
        return delete_watchlist_entry(conn, ticker, user_id)
