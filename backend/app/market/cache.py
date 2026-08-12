"""Thread-safe in-memory price cache."""

from __future__ import annotations

import time
from threading import Lock

from .models import PriceUpdate, normalize_ticker


class PriceCache:
    """Thread-safe store of the latest price for each ticker.

    Writers: SimulatorDataSource or MassiveDataSource (exactly one at a time).
    Readers: SSE streaming endpoint, portfolio valuation, trade execution, LLM context.

    Uses a threading.Lock rather than an asyncio.Lock because MassiveDataSource
    writes from an asyncio.to_thread worker on a real OS thread, which an
    asyncio lock would not protect.
    """

    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        #: When *we* last wrote each ticker, on the monotonic clock. Deliberately
        #: not `PriceUpdate.timestamp`, which is the venue's trade time: on
        #: Massive that is hours old the moment the market closes, and bounding
        #: it would refuse every trade out of hours. What detects a wedged
        #: poller is how long since anything arrived, which only receipt time
        #: can say. Monotonic, so a clock adjustment cannot make a live feed
        #: look stale.
        self._received: dict[str, float] = {}
        self._lock = Lock()
        self._version: int = 0  # monotonic; +1 per update

        #: How old an entry may be before `is_stale` reports it, in seconds, or
        #: None for no bound. Stamped by whichever source is writing — see
        #: `MarketDataSource.quote_staleness_limit`. A cache nobody is writing
        #: keeps None, so tests that populate it by hand are unaffected.
        self.staleness_limit: float | None = None

    def update(
        self,
        ticker: str,
        price: float,
        timestamp: float | None = None,
        previous_close: float | None = None,
    ) -> PriceUpdate:
        """Record a new price. Returns the created PriceUpdate.

        `previous_price` is computed here, so callers pass a bare float and
        cannot construct an inconsistent update. On the first update for a
        ticker, previous_price == price, so direction is 'flat' and the UI does
        not flash on page load.

        `previous_close` is sticky: pass it once (or on every poll) and it is
        carried forward on subsequent updates that omit it.
        """
        ticker = normalize_ticker(ticker)
        with self._lock:
            # NOTE: `is None`, not `or` — a legitimate timestamp of 0.0 is falsy.
            ts = time.time() if timestamp is None else timestamp
            prev = self._prices.get(ticker)

            previous_price = prev.price if prev else price
            close = previous_close
            if close is None and prev is not None:
                close = prev.previous_close

            update = PriceUpdate(
                ticker=ticker,
                price=round(price, 2),
                previous_price=round(previous_price, 2),
                timestamp=ts,
                previous_close=round(close, 2) if close is not None else None,
            )
            self._prices[ticker] = update
            self._received[ticker] = time.monotonic()
            self._version += 1
            return update

    def get(self, ticker: str) -> PriceUpdate | None:
        """Latest price for a single ticker, or None if unknown."""
        with self._lock:
            return self._prices.get(normalize_ticker(ticker))

    def get_price(self, ticker: str) -> float | None:
        """Convenience: just the float, or None if the ticker is unknown."""
        update = self.get(ticker)
        return update.price if update else None

    def get_all(self) -> dict[str, PriceUpdate]:
        """Snapshot of all current prices. Shallow copy — safe to iterate."""
        with self._lock:
            return dict(self._prices)

    def remove(self, ticker: str) -> None:
        """Remove a ticker from the cache (e.g. when removed from the watchlist)."""
        with self._lock:
            ticker = normalize_ticker(ticker)
            self._prices.pop(ticker, None)
            self._received.pop(ticker, None)

    def age_of(self, ticker: str) -> float | None:
        """Seconds since this cache last received a price for `ticker`.

        None when the ticker has never been written. Measured from receipt, not
        from the quote's own timestamp — see `_received`.
        """
        with self._lock:
            received = self._received.get(normalize_ticker(ticker))
        return None if received is None else time.monotonic() - received

    def is_stale(self, ticker: str) -> bool:
        """Has this entry outlived what its source promised?

        A factual question about the cache's own bookkeeping — what to *do*
        about a stale quote is a money rule, and lives in `app.portfolio`.

        False when no source has stamped a limit, and False for a ticker never
        written: "no price at all" is a different refusal with a better message.
        """
        limit = self.staleness_limit
        if limit is None:
            return False
        age = self.age_of(ticker)
        return age is not None and age > limit

    @property
    def version(self) -> int:
        """Monotonic counter for SSE change detection."""
        with self._lock:
            return self._version

    def __len__(self) -> int:
        with self._lock:
            return len(self._prices)

    def __contains__(self, ticker: str) -> bool:
        with self._lock:
            return normalize_ticker(ticker) in self._prices
