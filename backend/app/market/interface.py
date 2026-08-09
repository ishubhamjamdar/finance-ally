"""Abstract interface for market data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable


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

    #: Venue state, where the source knows it: "open" | "closed" |
    #: "extended-hours", or None when the concept does not apply (the
    #: simulator always trades). Declared here rather than on the one
    #: implementation that populates it, so consumers can read
    #: `source.market_status` without getattr() or isinstance().
    market_status: str | None = None

    #: Called when a source hits a failure that retrying cannot fix, to let the
    #: application swap in a working source mid-session. Assign after
    #: construction. A source that cannot fail permanently simply never calls it.
    on_permanent_failure: Callable[[Exception], Awaitable[None]] | None = None

    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Begin producing updates. Call exactly once; twice is undefined.

        Populates the cache for as many tickers as it can before returning.
        Raises on a failure that retrying cannot fix.

        It deliberately does NOT promise that any ticker got a price: a
        transient fetch failure leaves the cache empty and is worth retrying
        rather than aborting. Callers that need prices must verify by reading
        the cache — see `factory.start_market_data`.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Cancel background work and release resources.

        Idempotent, and safe to call when start() was never called.
        """

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Add to the active set. No-op if already present.

        When a price appears is source-specific: immediately for the simulator,
        up to one poll interval later for a polling source. Callers must not
        assume the ticker is priceable the moment this returns.
        """

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Remove from the active set and drop it from the cache. No-op if absent."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Currently tracked tickers. Local state only — never does I/O."""
