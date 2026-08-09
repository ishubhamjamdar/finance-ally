"""Factory for creating and starting market data sources."""

from __future__ import annotations

import logging
import os

from .cache import PriceCache
from .events import EventLog
from .interface import MarketDataSource
from .massive_client import MassiveDataSource
from .simulator import SimulatorDataSource

logger = logging.getLogger(__name__)


def create_market_data_source(
    price_cache: PriceCache,
    event_log: EventLog | None = None,
) -> MarketDataSource:
    """Select a source from the environment. Returns it UNSTARTED.

    - MASSIVE_API_KEY set and non-empty  -> MassiveDataSource
    - otherwise                          -> SimulatorDataSource

    .strip() matters: .env files routinely contain `MASSIVE_API_KEY=` or a
    stray space, and per PLAN.md §5 whitespace means "absent".

    This is the only module that reads the environment.
    """
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()

    if api_key:
        poll_interval = float(os.environ.get("MASSIVE_POLL_INTERVAL", "15"))
        logger.info("Market data source: Massive API (real data, %.0fs poll)", poll_interval)
        return MassiveDataSource(
            api_key=api_key,
            price_cache=price_cache,
            poll_interval=poll_interval,
            event_log=event_log,
        )

    logger.info("Market data source: GBM Simulator")
    return SimulatorDataSource(
        price_cache=price_cache,
        update_interval=float(os.environ.get("SIM_UPDATE_INTERVAL", "0.5")),
        event_probability=float(os.environ.get("SIM_EVENT_PROBABILITY", "2e-5")),
        event_log=event_log,
    )


async def start_market_data(
    price_cache: PriceCache,
    tickers: list[str],
    event_log: EventLog | None = None,
) -> MarketDataSource:
    """Create AND start a source, falling back to the simulator if Massive fails.

    A key alone does not mean live prices are available: the free Basic plan
    excludes snapshots entirely. Verify by outcome, not by configuration —
    otherwise the worst failure mode (valid free-tier key, permanently empty
    watchlist, no error anywhere) looks exactly like a healthy app.
    """
    source = create_market_data_source(price_cache, event_log=event_log)

    if isinstance(source, SimulatorDataSource):
        await source.start(tickers)
        return source

    try:
        await source.start(tickers)
        if len(price_cache) == 0:
            raise RuntimeError("Massive returned no usable prices (plan entitlement?)")
        return source
    except Exception as exc:
        logger.error("Massive unavailable (%s) — falling back to the simulator", exc)
        await source.stop()

    fallback = SimulatorDataSource(price_cache=price_cache, event_log=event_log)
    await fallback.start(tickers)
    return fallback
