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
        self._lock = Lock()
        self._version: int = 0  # monotonic; +1 per update

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
            self._prices.pop(normalize_ticker(ticker), None)

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
