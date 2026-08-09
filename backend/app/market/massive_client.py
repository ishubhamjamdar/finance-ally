"""Massive (Polygon.io) API client for real market data."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from massive import RESTClient
from massive.rest.models import SnapshotMarketType

from .cache import PriceCache
from .events import EventLog
from .interface import MarketDataSource, PermanentMarketDataError
from .models import normalize_ticker

logger = logging.getLogger(__name__)


def to_epoch_seconds(raw: int | float | None) -> float | None:
    """Normalise s / ms / us / ns to epoch seconds by order of magnitude.

    Massive is inconsistent across endpoints (snapshot `updated` and
    `sip_timestamp` are ns; Agg timestamps are ms; MinuteSnapshot is documented
    both ways), so infer rather than trust.
    """
    if not raw:
        return None
    value = float(raw)
    for divisor in (1.0, 1e3, 1e6, 1e9):
        candidate = value / divisor
        if 1e9 < candidate < 4e9:  # ~2001 .. 2096
            return candidate
    return None


def extract_price(snap) -> float | None:
    """Freshest available price, degrading gracefully across plan tiers."""
    if snap.last_trade is not None and snap.last_trade.price:
        return snap.last_trade.price  # Developer+ : actual last trade
    if snap.min is not None and snap.min.close:
        return snap.min.close  # Starter    : latest minute bar
    if snap.day is not None and snap.day.close:
        return snap.day.close  # today's bar so far
    if snap.prev_day is not None and snap.prev_day.close:
        return snap.prev_day.close  # pre-open / stale fallback
    return None


def extract_timestamp(snap) -> float | None:
    """Epoch seconds for the price returned by extract_price().

    LastTrade exposes `sip_timestamp` (nanoseconds), NOT `timestamp`. Reading
    `.timestamp` raises AttributeError on every snapshot, which is how the
    previous implementation left the cache permanently empty.
    """
    if snap.last_trade is not None:
        ts = to_epoch_seconds(snap.last_trade.sip_timestamp)
        if ts is not None:
            return ts
    return to_epoch_seconds(snap.updated)


def extract_previous_close(snap) -> float | None:
    """Previous session's close — the day-change baseline."""
    if snap.prev_day is not None and snap.prev_day.close:
        return snap.prev_day.close
    return None


_PERMANENT_MARKERS = (
    "401",
    "403",
    "unauthorized",
    "not authorized",
    "not entitled",
    "forbidden",
    "invalid api key",
)


def is_permanent_failure(exc: Exception) -> bool:
    """Whether an error is worth retrying.

    BadResponse is a single flat type whose message is the raw body, so the
    only way to distinguish 'bad key / no entitlement' from 'try again later'
    is to inspect the text. 429 and 5xx are deliberately NOT listed — those are
    transient and the SDK already retries them.
    """
    text = str(exc).lower()
    return any(marker in text for marker in _PERMANENT_MARKERS)


class MassiveDataSource(MarketDataSource):
    """MarketDataSource backed by the Massive (Polygon.io) REST API.

    Polls GET /v2/snapshot/locale/us/markets/stocks/tickers for every watched
    ticker in one call, so request count stays flat as the watchlist grows.

    Poll interval by plan:
      Basic (free)        snapshots EXCLUDED — this source cannot work
      Starter / Developer 15-min delayed  -> 15 s is plenty
      Advanced+           real-time       -> 2-5 s
    """

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float = 15.0,
        connect_timeout: float = 5.0,
        read_timeout: float = 5.0,
        status_refresh_polls: int = 20,
        event_log: EventLog | None = None,
        on_permanent_failure: Callable[[Exception], Awaitable[None]] | None = None,
    ) -> None:
        self._api_key = api_key
        self._cache = price_cache
        self._interval = poll_interval
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._status_refresh_polls = status_refresh_polls
        self._event_log = event_log
        self._on_permanent_failure = on_permanent_failure

        self._tickers: list[str] = []
        self._task: asyncio.Task | None = None
        self._client: RESTClient | None = None
        self._poll_count = 0
        self.market_status: str | None = None  # "open" | "closed" | "extended-hours"
        self.last_poll_at: float | None = None

    async def start(self, tickers: list[str]) -> None:
        # Normalised HERE too, not only in add/remove — a lower-case watchlist
        # row from SQLite would otherwise silently produce no data.
        self._tickers = [normalize_ticker(t) for t in tickers]
        self._client = RESTClient(
            api_key=self._api_key,
            connect_timeout=self._connect_timeout,  # tighter than the 10 s default, so a
            read_timeout=self._read_timeout,  # hung request can't outlive its interval
        )

        # First poll happens inline so the caller can decide whether this source
        # is usable before committing to it (see factory.start_market_data).
        # A permanent failure propagates; a transient one is swallowed and retried.
        await self._poll_once()
        await self._refresh_market_status()

        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info(
            "Massive poller started: %d tickers, %.1fs interval, market=%s",
            len(self._tickers),
            self._interval,
            self.market_status,
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._client = None
        logger.info("Massive poller stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if ticker not in self._tickers:
            self._tickers.append(ticker)
            logger.info("Massive: added %s (priced on next poll, <= %.0fs)", ticker, self._interval)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        self._tickers = [t for t in self._tickers if t != ticker]
        self._cache.remove(ticker)
        logger.info("Massive: removed ticker %s", ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- Internals ---

    async def _poll_loop(self) -> None:
        """Sleep-then-poll: start() already did the first one."""
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self._poll_once()
                self._poll_count += 1
                if self._poll_count % self._status_refresh_polls == 0:
                    await self._refresh_market_status()
            except PermanentMarketDataError as exc:
                # Retrying a 401/403 every 15 s forever is how a broken key
                # becomes a permanently empty UI with no signal. Stop, shout,
                # and let the app fail over.
                logger.error("Massive permanently unavailable, stopping poller: %s", exc)
                if self._on_permanent_failure:
                    await self._on_permanent_failure(exc)
                return

    async def _poll_once(self) -> int:
        """One poll cycle. Returns the number of tickers updated.

        Raises PermanentMarketDataError on 401/403-class failures. Transient
        failures are logged and swallowed — the cache keeps serving the last
        known prices, which is strictly better than blanking the UI.
        """
        if not self._tickers or not self._client:
            return 0

        try:
            # The Massive RESTClient is synchronous (urllib3) — run in a thread
            # so it never blocks the event loop.
            snapshots = await asyncio.to_thread(self._fetch_snapshots)
        except Exception as exc:
            if is_permanent_failure(exc):
                raise PermanentMarketDataError(str(exc)) from exc
            logger.warning("Massive poll failed (will retry in %.0fs): %s", self._interval, exc)
            return 0

        processed = 0
        for snap in snapshots:
            ticker = getattr(snap, "ticker", None)
            price = extract_price(snap)
            if not ticker or price is None:
                logger.warning("No usable price for %s", ticker or "???")
                continue
            self._cache.update(
                ticker=ticker,
                price=price,
                timestamp=extract_timestamp(snap) or time.time(),
                previous_close=extract_previous_close(snap),
            )
            processed += 1

        self.last_poll_at = time.time()
        logger.debug("Massive poll: updated %d/%d tickers", processed, len(self._tickers))
        return processed

    async def _refresh_market_status(self) -> None:
        """With real data, prices are static outside 09:30-16:00 ET. Surface that
        rather than hide it — never synthesise fake movement onto real prices.
        """
        if not self._client:
            return
        try:
            status = await asyncio.to_thread(self._client.get_market_status)
            self.market_status = getattr(status, "market", None)
        except Exception as exc:
            logger.debug("Market status unavailable: %s", exc)

    def _fetch_snapshots(self) -> list:
        """Synchronous SDK call. Runs in a worker thread."""
        return self._client.get_snapshot_all(
            market_type=SnapshotMarketType.STOCKS,
            tickers=self._tickers,
        )
