"""Factory for creating and starting market data sources."""

from __future__ import annotations

import logging
import os

from .cache import PriceCache
from .events import EventLog
from .interface import MarketDataSource
from .massive_client import DEFAULT_POLL_INTERVAL, MassiveDataSource
from .models import normalize_ticker
from .simulator import (
    DEFAULT_EVENT_PROBABILITY,
    DEFAULT_UPDATE_INTERVAL,
    SimulatorDataSource,
)

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    """Read a float from the environment, tolerating blank and malformed values.

    `.env` files routinely carry `MASSIVE_POLL_INTERVAL=` with nothing after
    the sign — the same habit the API-key `.strip()` below defends against.
    A bare `float("")` raises ValueError, which would propagate out of
    start_market_data and take the whole app down at boot over a tuning knob
    that has a perfectly good default.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number — falling back to %s", name, raw, default)
        return default


def create_simulator_source(
    price_cache: PriceCache,
    event_log: EventLog | None = None,
) -> SimulatorDataSource:
    """Build a simulator from the environment. Returns it UNSTARTED.

    Shared by the primary path and the Massive fallback, so a user who tunes
    SIM_UPDATE_INTERVAL does not silently get stock defaults the moment their
    API key stops working.

    Public because mid-run failover needs it: the lifespan's
    `on_permanent_failure` handler must build a simulator *specifically*, not
    re-run source selection, which would read MASSIVE_API_KEY and hand back the
    source that just died.
    """
    return SimulatorDataSource(
        price_cache=price_cache,
        update_interval=_env_float("SIM_UPDATE_INTERVAL", DEFAULT_UPDATE_INTERVAL),
        event_probability=_env_float("SIM_EVENT_PROBABILITY", DEFAULT_EVENT_PROBABILITY),
        event_log=event_log,
    )


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
        poll_interval = _env_float("MASSIVE_POLL_INTERVAL", DEFAULT_POLL_INTERVAL)
        logger.info("Market data source: Massive API (real data, %.0fs poll)", poll_interval)
        return MassiveDataSource(
            api_key=api_key,
            price_cache=price_cache,
            poll_interval=poll_interval,
        )

    logger.info("Market data source: GBM Simulator")
    return create_simulator_source(price_cache, event_log)


def _verify_priced(price_cache: PriceCache, tickers: list[str]) -> None:
    """Raise if none of the requested tickers got a price; warn on partial cover.

    Checks the REQUESTED tickers rather than len(cache): a non-empty cache
    proves nothing when the cache is shared or this runs twice, and a global
    count of 1 would accept a plan that priced one ticker out of ten — the
    partial-entitlement case this exists to catch.

    Source-agnostic on purpose. "Did the caller get what it asked for?" is the
    right question for any source, including ones added later.
    """
    wanted = [normalize_ticker(t) for t in tickers]
    missing = [t for t in wanted if price_cache.get(t) is None]
    if wanted and len(missing) == len(wanted):
        raise RuntimeError("no usable prices for any requested ticker (plan entitlement?)")
    if missing:
        logger.warning(
            "Priced only %d of %d tickers; no data for: %s",
            len(wanted) - len(missing),
            len(wanted),
            ", ".join(sorted(missing)),
        )


async def start_market_data(
    price_cache: PriceCache,
    tickers: list[str],
    event_log: EventLog | None = None,
) -> MarketDataSource:
    """Create AND start a source, falling back to the simulator if it fails.

    A key alone does not mean live prices are available: the free Basic plan
    excludes snapshots entirely. Verify by outcome, not by configuration —
    otherwise the worst failure mode (valid free-tier key, permanently empty
    watchlist, no error anywhere) looks exactly like a healthy app.

    Every source is started and verified the same way. The isinstance check
    guards only the recursion: when the simulator is what failed, there is
    nothing left to fall back to.
    """
    source = create_market_data_source(price_cache, event_log=event_log)

    try:
        await source.start(tickers)
        _verify_priced(price_cache, tickers)
        return source
    except Exception as exc:
        if isinstance(source, SimulatorDataSource):
            raise
        logger.error("Massive unavailable (%s) — falling back to the simulator", exc)
        await source.stop()
        fallback = create_simulator_source(price_cache, event_log)
        await fallback.start(tickers)
        return fallback
