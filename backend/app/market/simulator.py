"""GBM-based market simulator."""

from __future__ import annotations

import asyncio
import logging
import math
import random

import numpy as np

from .cache import PriceCache
from .events import EventLog
from .interface import MarketDataSource
from .models import MarketEvent, normalize_ticker
from .seed_prices import (
    CORRELATION_GROUPS,
    CROSS_GROUP_CORR,
    DEFAULT_PARAMS,
    INTRA_FINANCE_CORR,
    INTRA_TECH_CORR,
    SEED_PRICES,
    TICKER_PARAMS,
    TSLA_CORR,
    UNKNOWN_PRICE_RANGE,
)

logger = logging.getLogger(__name__)

# ~1 event per ticker per 46,800-tick session. At the previous 0.001 the shock
# process contributed ~24% daily volatility to every ticker alike, swamping
# sigma and making TICKER_PARAMS dead config.
DEFAULT_EVENT_PROBABILITY = 2e-5

# Simulator tick, seconds. GBMSimulator.DEFAULT_DT is derived from it.
DEFAULT_UPDATE_INTERVAL = 0.5


class GBMSimulator:
    """Geometric Brownian Motion simulator for correlated stock prices.

        S(t+dt) = S(t) * exp((mu - sigma^2/2) * dt + sigma * sqrt(dt) * Z)

    Z is correlated across tickers via the Cholesky factor of a sector-based
    correlation matrix. State is one float per ticker, so step() stays cheap at
    2 Hz regardless of how long the process has been running.
    """

    TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600  # 5,896,800
    DEFAULT_DT = DEFAULT_UPDATE_INTERVAL / TRADING_SECONDS_PER_YEAR  # ~8.48e-8

    def __init__(
        self,
        tickers: list[str],
        dt: float = DEFAULT_DT,
        event_probability: float = DEFAULT_EVENT_PROBABILITY,
        rng: np.random.Generator | None = None,
        py_rng: random.Random | None = None,
    ) -> None:
        self._dt = dt
        self._event_prob = event_probability
        # Injected RNGs so tests get deterministic paths without seeding globals.
        self._rng = rng if rng is not None else np.random.default_rng()
        self._py_rng = py_rng if py_rng is not None else random.Random()

        # _tickers is ORDERED and indexes the rows of the Cholesky factor:
        # z_correlated[i] must line up with _tickers[i]. Any reordering must
        # rebuild the factor.
        self._tickers: list[str] = []
        self._prices: dict[str, float] = {}
        self._params: dict[str, dict[str, float]] = {}
        self._session_open: dict[str, float] = {}  # serves as previous_close
        self._events: list[MarketEvent] = []
        self._cholesky: np.ndarray | None = None

        for ticker in tickers:
            self._add_ticker_internal(ticker)
        self._rebuild_cholesky()  # once, not once per ticker

    # --- Public API ---

    def step(self) -> dict[str, float]:
        """Advance all tickers one step. Returns {ticker: new_price}.

        Hot path — called every 500 ms. Shocks generated here are appended to an
        internal buffer; call drain_events() to collect them.
        """
        n = len(self._tickers)
        if n == 0:
            return {}

        z = self._rng.standard_normal(n)
        if self._cholesky is not None:
            z = self._cholesky @ z

        result: dict[str, float] = {}
        for i, ticker in enumerate(self._tickers):
            params = self._params[ticker]
            mu, sigma = params["mu"], params["sigma"]

            drift = (mu - 0.5 * sigma**2) * self._dt
            diffusion = sigma * math.sqrt(self._dt) * z[i]
            self._prices[ticker] *= math.exp(drift + diffusion)

            if self._py_rng.random() < self._event_prob:
                self._apply_shock(ticker)

            result[ticker] = round(self._prices[ticker], 2)

        return result

    def drain_events(self) -> list[MarketEvent]:
        """Take the shocks generated since the last call."""
        events, self._events = self._events, []
        return events

    def add_ticker(self, ticker: str) -> None:
        """Add a ticker to the simulation. Rebuilds the correlation matrix."""
        ticker = normalize_ticker(ticker)
        if ticker in self._prices:
            return
        self._add_ticker_internal(ticker)
        self._rebuild_cholesky()

    def remove_ticker(self, ticker: str) -> None:
        """Remove a ticker from the simulation. Rebuilds the correlation matrix."""
        ticker = normalize_ticker(ticker)
        if ticker not in self._prices:
            return
        self._tickers.remove(ticker)
        del self._prices[ticker]
        del self._params[ticker]
        self._session_open.pop(ticker, None)
        self._rebuild_cholesky()

    def get_price(self, ticker: str) -> float | None:
        """Current price for a ticker, or None if not tracked."""
        return self._prices.get(normalize_ticker(ticker))

    def get_previous_close(self, ticker: str) -> float | None:
        """The price this ticker opened the session at — the day-change baseline."""
        return self._session_open.get(normalize_ticker(ticker))

    def get_tickers(self) -> list[str]:
        """Public so SimulatorDataSource never reaches into _tickers."""
        return list(self._tickers)

    # --- Internals ---

    def _apply_shock(self, ticker: str) -> None:
        """A sudden 2-5% move. Log-space so up and down are mirror images.

        `*= (1 ± m)` would compound with a positive skew: a +5% followed by a
        -5% does not return to the starting price.
        """
        magnitude = self._py_rng.uniform(0.02, 0.05)
        sign = self._py_rng.choice((-1.0, 1.0))
        self._prices[ticker] *= math.exp(magnitude * sign)
        event = MarketEvent(
            ticker=ticker,
            magnitude_percent=round(sign * magnitude * 100, 2),
            price=round(self._prices[ticker], 2),
        )
        self._events.append(event)
        logger.info("Market event: %s %+.1f%%", ticker, event.magnitude_percent)

    def _add_ticker_internal(self, ticker: str) -> None:
        """Add without rebuilding Cholesky — lets __init__ rebuild once for N tickers."""
        ticker = normalize_ticker(ticker)
        if ticker in self._prices:
            return
        price = SEED_PRICES.get(ticker) or self._py_rng.uniform(*UNKNOWN_PRICE_RANGE)
        self._tickers.append(ticker)
        self._prices[ticker] = price
        self._session_open[ticker] = round(price, 2)
        # dict(...) — a shared reference would let tuning one runtime ticker
        # mutate the defaults for every other one.
        self._params[ticker] = dict(TICKER_PARAMS.get(ticker, DEFAULT_PARAMS))

    def _rebuild_cholesky(self) -> None:
        """O(n^2) build + O(n^3) factor, on add/remove only — never on the hot path."""
        n = len(self._tickers)
        if n <= 1:
            self._cholesky = None  # cholesky of 1x1 is pointless, of 0x0 raises
            return

        corr = np.eye(n)
        for i in range(n):
            for j in range(i + 1, n):
                rho = self._pairwise_correlation(self._tickers[i], self._tickers[j])
                corr[i, j] = corr[j, i] = rho

        try:
            self._cholesky = np.linalg.cholesky(corr)
        except np.linalg.LinAlgError:
            # Cannot happen with the group-based rule, but a crash on
            # add_ticker() would be user-visible. Degrade to independent draws.
            logger.error("Correlation matrix not positive-definite — using independent draws")
            self._cholesky = None

    @staticmethod
    def _pairwise_correlation(t1: str, t2: str) -> float:
        """Correlation between two tickers, from sector membership only.

        Hand-editing individual pairs can break positive-definiteness and crash
        the next add_ticker(); keep this a function of group membership.
        """
        tech = CORRELATION_GROUPS["tech"]
        finance = CORRELATION_GROUPS["finance"]

        # Checked FIRST: TSLA is in the tech set but stays weakly correlated.
        if t1 == "TSLA" or t2 == "TSLA":
            return TSLA_CORR
        if t1 in tech and t2 in tech:
            return INTRA_TECH_CORR
        if t1 in finance and t2 in finance:
            return INTRA_FINANCE_CORR
        return CROSS_GROUP_CORR


class SimulatorDataSource(MarketDataSource):
    """MarketDataSource backed by the GBM simulator.

    Runs a background asyncio task that steps the simulation every
    `update_interval` seconds and writes the results to the PriceCache.
    """

    def __init__(
        self,
        price_cache: PriceCache,
        update_interval: float = DEFAULT_UPDATE_INTERVAL,
        event_probability: float = DEFAULT_EVENT_PROBABILITY,
        event_log: EventLog | None = None,
    ) -> None:
        self._cache = price_cache
        self._interval = update_interval
        self._event_prob = event_probability
        self._event_log = event_log
        self._sim: GBMSimulator | None = None
        self._task: asyncio.Task | None = None

    async def start(self, tickers: list[str]) -> None:
        tickers = [normalize_ticker(t) for t in tickers]
        # dt is derived from the actual tick rate, not left at the 500 ms
        # default. Otherwise SIM_UPDATE_INTERVAL=2 keeps 500 ms-sized moves but
        # applies them a quarter as often, halving realised volatility so
        # `sigma` no longer means annualised volatility.
        self._sim = GBMSimulator(
            tickers=tickers,
            dt=self._interval / GBMSimulator.TRADING_SECONDS_PER_YEAR,
            event_probability=self._event_prob,
        )

        # Seed the cache BEFORE the loop spawns, so the first SSE frame and the
        # first trade both have prices — no empty-watchlist flash on load.
        for ticker in tickers:
            self._write(ticker)

        self._task = asyncio.create_task(self._run_loop(), name="simulator-loop")
        logger.info("Simulator started with %d tickers", len(tickers))

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass  # expected: we cancelled it
        self._task = None
        logger.info("Simulator stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if not self._sim:
            return
        self._sim.add_ticker(ticker)
        self._write(ticker)  # priceable at once, not after up to 500 ms
        logger.info("Simulator: added ticker %s", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if self._sim:
            self._sim.remove_ticker(ticker)
        self._cache.remove(ticker)
        logger.info("Simulator: removed ticker %s", ticker)

    def get_tickers(self) -> list[str]:
        return self._sim.get_tickers() if self._sim else []

    # --- Internals ---

    def _write(self, ticker: str, price: float | None = None) -> None:
        """Write one ticker to the cache, carrying its session-open baseline."""
        if not self._sim:
            return
        value = price if price is not None else self._sim.get_price(ticker)
        if value is None:
            return
        self._cache.update(
            ticker=ticker,
            price=value,
            previous_close=self._sim.get_previous_close(ticker),
        )

    async def _run_loop(self) -> None:
        """Step, write, publish events, sleep.

        The try/except is INSIDE the while, not around it: a single bad step
        logs and the loop survives. Wrapping the while would silently end all
        price updates for the lifetime of the process.
        """
        while True:
            try:
                if self._sim:
                    for ticker, price in self._sim.step().items():
                        self._write(ticker, price)
                    if self._event_log is not None:
                        self._event_log.extend(self._sim.drain_events())
            except Exception:
                logger.exception("Simulator step failed")
            await asyncio.sleep(self._interval)
