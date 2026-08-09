"""Data models for market data."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


def normalize_ticker(ticker: str) -> str:
    """Canonical ticker form: upper-case, stripped.

    Applied at every entry point — REST handlers, LLM tool calls, source
    constructors. Massive tickers are case-sensitive, and a lower-case row from
    SQLite must not silently produce a ticker that never gets a price.
    """
    return ticker.strip().upper()


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """Immutable snapshot of a single ticker's price at a point in time."""

    ticker: str
    price: float
    previous_price: float  # previous TICK, not previous close
    timestamp: float = field(default_factory=time.time)  # epoch SECONDS
    previous_close: float | None = None  # last session's close, if known

    # --- tick-over-tick (drives the green/red flash) ---

    @property
    def change(self) -> float:
        """Absolute price change from the previous tick."""
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
        """Percentage change from the previous tick."""
        if self.previous_price == 0:
            return 0.0
        return round((self.price - self.previous_price) / self.previous_price * 100, 4)

    @property
    def direction(self) -> str:
        """'up', 'down', or 'flat'."""
        if self.price > self.previous_price:
            return "up"
        if self.price < self.previous_price:
            return "down"
        return "flat"

    # --- session-over-session (drives the watchlist's "daily change %") ---

    @property
    def day_change(self) -> float | None:
        """Absolute change since the previous close, or None if unknown."""
        if not self.previous_close:
            return None
        return round(self.price - self.previous_close, 4)

    @property
    def day_change_percent(self) -> float | None:
        """Percentage change since the previous close, or None if unknown."""
        if not self.previous_close:
            return None
        return round((self.price - self.previous_close) / self.previous_close * 100, 4)

    def to_dict(self) -> dict:
        """Serialize for JSON / SSE transmission."""
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "timestamp": self.timestamp,
            "change": self.change,
            "change_percent": self.change_percent,
            "direction": self.direction,
            "previous_close": self.previous_close,
            "day_change": self.day_change,
            "day_change_percent": self.day_change_percent,
        }


@dataclass(frozen=True, slots=True)
class MarketEvent:
    """A notable move worth surfacing in the UI (simulator shock, or a large real move)."""

    ticker: str
    magnitude_percent: float  # signed: -3.4 means down 3.4%
    price: float
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "magnitude_percent": self.magnitude_percent,
            "price": self.price,
            "timestamp": self.timestamp,
        }
