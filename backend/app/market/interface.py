"""Abstract interface for market data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod


class PermanentMarketDataError(RuntimeError):
    """A failure that retrying cannot fix — bad key, plan lacks entitlement.

    Raised by a source to tell the caller to stop polling and fail over, as
    opposed to a transient error which is logged and retried.
    """


class MarketDataSource(ABC):
    """Contract for market data providers.

    Implementations push price updates into a shared PriceCache on their own
    schedule. Downstream code never calls a source for a price — it reads the
    cache. Lifecycle:

        source = create_market_data_source(cache)
        await source.start(["AAPL", "GOOGL", ...])
        await source.add_ticker("TSLA")
        await source.remove_ticker("GOOGL")
        await source.stop()
    """

    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Begin producing updates. Call exactly once; twice is undefined.

        Must populate the cache for at least one ticker before returning, or
        raise, so the caller can decide whether the source is usable.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Cancel background work and release resources.

        Idempotent, and safe to call when start() was never called.
        """

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Add to the active set. No-op if already present."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Remove from the active set and drop it from the cache. No-op if absent."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Currently tracked tickers. Local state only — never does I/O."""
