# Market Data Backend — Detailed Design

Implementation-ready design for FinAlly's market data subsystem: one unified interface, two
implementations (GBM simulator and Massive REST API), a shared price cache, and the SSE endpoint
that pushes prices to the browser.

Everything in this document lives under `backend/app/market/`.

**Relationship to the other planning docs**

| Doc | Role |
|---|---|
| `MARKET_INTERFACE.md` | The contract and its rationale (why push, why a cache) |
| `MARKET_SIMULATOR.md` | GBM maths, measured volatility, correlation analysis |
| `MASSIVE_API.md` | Verified SDK field names, plan entitlements, timestamp units |
| `MARKET_DATA_SUMMARY.md` | Status of what is currently built |
| **This doc** | The full target implementation, module by module, with code |

This is the **target** design. It is the as-built code plus the corrections that
`MARKET_INTERFACE.md` §7, `MASSIVE_API.md` §11 and `MARKET_SIMULATOR.md` §6 identify as required.
§16 lists exactly what changes relative to the code on disk today.

---

## Table of Contents

1. [Architecture](#1-architecture)
2. [File layout](#2-file-layout)
3. [Configuration](#3-configuration)
4. [`models.py` — PriceUpdate, MarketEvent, normalisation](#4-modelspy--priceupdate-marketevent-normalisation)
5. [`cache.py` — PriceCache](#5-cachepy--pricecache)
6. [`events.py` — EventLog](#6-eventspy--eventlog)
7. [`interface.py` — MarketDataSource ABC](#7-interfacepy--marketdatasource-abc)
8. [`seed_prices.py` — constants](#8-seed_pricespy--constants)
9. [`simulator.py` — GBM engine + async source](#9-simulatorpy--gbm-engine--async-source)
10. [`massive_client.py` — REST poller](#10-massive_clientpy--rest-poller)
11. [`factory.py` — source selection and failover](#11-factorypy--source-selection-and-failover)
12. [`stream.py` — SSE endpoint](#12-streampy--sse-endpoint)
13. [FastAPI wiring and consumers](#13-fastapi-wiring-and-consumers)
14. [Frontend consumption](#14-frontend-consumption)
15. [Error handling and edge cases](#15-error-handling-and-edge-cases)
16. [Testing strategy](#16-testing-strategy)
17. [Delta from the current implementation](#17-delta-from-the-current-implementation)

---

## 1. Architecture

One background producer writes into a shared in-memory cache; every consumer reads from that cache.
Nothing downstream knows or cares which source is running.

```
                    ┌────────────────────────────┐
                    │  start_market_data()       │  reads MASSIVE_API_KEY
                    │  (factory.py)              │  fail-fast → fallback
                    └────────────┬───────────────┘
                     ┌───────────┴────────────┐
                     ▼                        ▼
          SimulatorDataSource          MassiveDataSource
          GBM, 500 ms tick             REST snapshot poll, 15 s
                     │                        │
                     └───────────┬────────────┘
                       writes    ▼
              ┌──────────────────────────────────┐
              │  PriceCache  (thread-safe)       │   EventLog (shocks)
              │  latest PriceUpdate per ticker   │   bounded ring buffer
              │  monotonic version counter       │   per-client cursors
              └──────────────────┬───────────────┘
                       reads     │
        ┌──────────────┬─────────┴────────┬──────────────────┐
        ▼              ▼                  ▼                  ▼
  SSE /api/stream   portfolio        trade execution     LLM chat
     /prices        valuation        (fill price)        context
```

### Invariants the whole subsystem depends on

| # | Invariant | Enforced by |
|---|---|---|
| 1 | Every price that leaves the layer is a `PriceUpdate` | Sources only write via `PriceCache.update()` |
| 2 | `timestamp` is **epoch seconds**, always | Each source normalises; `to_epoch_seconds()` in the Massive client |
| 3 | Tickers are upper-case with no whitespace | `normalize_ticker()` at every entry point |
| 4 | A consumer never awaits I/O to get a price | No `get_price()` on `MarketDataSource` |
| 5 | Prices are strictly positive | GBM is multiplicative; shocks applied in log space |
| 6 | A source failure degrades, never crashes | try/except inside loops, permanent-error failover |
| 7 | `len(simulator._tickers) == cholesky.shape[0]` | Every add/remove rebuilds the factor |

---

## 2. File layout

```
backend/app/market/
├── __init__.py          # public surface — import only from here
├── models.py            # PriceUpdate, MarketEvent, normalize_ticker
├── cache.py             # PriceCache
├── events.py            # EventLog (multi-client shock fan-out)
├── interface.py         # MarketDataSource ABC + PermanentMarketDataError
├── seed_prices.py       # constants only: seeds, GBM params, correlation groups
├── simulator.py         # GBMSimulator (pure maths) + SimulatorDataSource (async)
├── massive_client.py    # extraction helpers + MassiveDataSource (REST poller)
├── factory.py           # create_market_data_source(), start_market_data()
└── stream.py            # create_stream_router() — SSE
```

```python
# backend/app/market/__init__.py
"""Market data subsystem for FinAlly."""

from .cache import PriceCache
from .events import EventLog
from .factory import create_market_data_source, start_market_data
from .interface import MarketDataSource, PermanentMarketDataError
from .models import MarketEvent, PriceUpdate, normalize_ticker
from .stream import create_stream_router

__all__ = [
    "PriceUpdate",
    "MarketEvent",
    "normalize_ticker",
    "PriceCache",
    "EventLog",
    "MarketDataSource",
    "PermanentMarketDataError",
    "create_market_data_source",
    "start_market_data",
    "create_stream_router",
]
```

Rule: the rest of the backend imports `from app.market import ...` and never from a submodule.
`__init__.py` is the supported contract; everything else is free to move.

---

## 3. Configuration

| Env var | Default | Meaning |
|---|---|---|
| `MASSIVE_API_KEY` | *(empty)* | Non-empty after `.strip()` → try Massive; else simulator |
| `MASSIVE_POLL_INTERVAL` | `15` | Seconds between snapshot polls (2–5 on Advanced+) |
| `SIM_EVENT_PROBABILITY` | `2e-5` | Shock chance per ticker per tick (≈1 per session) |
| `SIM_UPDATE_INTERVAL` | `0.5` | Simulator tick, seconds |

Constructor defaults, not env-driven (change in code if ever needed):

| Parameter | Default | Where |
|---|---|---|
| `dt` | `0.5 / 5_896_800` ≈ `8.48e-8` | `GBMSimulator` |
| `connect_timeout` / `read_timeout` | `5.0` s | `MassiveDataSource` → `RESTClient` |
| SSE push interval | `0.5` s | `create_stream_router` |
| SSE heartbeat | `15` s | `create_stream_router` |
| SSE retry directive | `1000` ms | `_generate_events` |
| `EventLog` capacity | `200` events | `EventLog` |

`factory.py` is the **only** module that reads `os.environ`. Everything else takes parameters.

---

## 4. `models.py` — PriceUpdate, MarketEvent, normalisation

`PriceUpdate` is the only structure that leaves the layer. Immutable, with derived values as
properties so they can never disagree with the prices they came from.

```python
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
    previous_price: float                                 # previous TICK, not previous close
    timestamp: float = field(default_factory=time.time)   # epoch SECONDS
    previous_close: float | None = None                   # last session's close, if known

    # --- tick-over-tick (drives the green/red flash) ---

    @property
    def change(self) -> float:
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
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
        if not self.previous_close:
            return None
        return round(self.price - self.previous_close, 4)

    @property
    def day_change_percent(self) -> float | None:
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
    magnitude_percent: float                              # signed: -3.4 means down 3.4%
    price: float
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "magnitude_percent": self.magnitude_percent,
            "price": self.price,
            "timestamp": self.timestamp,
        }
```

### Why two notions of change

`change`/`direction` are **tick-over-tick** — they exist to flash a cell green or red for 500 ms.
`day_change_percent` is **session-over-session** — the number the watchlist column labels "daily
change %". Conflating them is the single easiest way to ship a watchlist that shows ±0.004%.

`previous_close` is optional because it is genuinely unavailable in some states (a runtime-added
ticker on a Massive plan before the first poll returns `prev_day`). Consumers render `—` when it is
`None` rather than inventing a baseline.

**Sources of `previous_close`:**

| Source | Value |
|---|---|
| Simulator | The ticker's seed price, captured at `start()` / `add_ticker()` |
| Massive `/v2/snapshot` | `snap.prev_day.close` |
| Massive `/v3/snapshot` | `snap.session.previous_close` |

---

## 5. `cache.py` — PriceCache

Single writer, many readers. A `threading.Lock` — not `asyncio.Lock` — because
`MassiveDataSource` writes from an `asyncio.to_thread` worker running on a real OS thread, which an
asyncio lock would not protect.

```python
"""Thread-safe in-memory price cache."""

from __future__ import annotations

import time
from threading import Lock

from .models import PriceUpdate, normalize_ticker


class PriceCache:
    """Thread-safe store of the latest price for each ticker.

    Writers: SimulatorDataSource or MassiveDataSource (exactly one at a time).
    Readers: SSE streaming endpoint, portfolio valuation, trade execution, LLM context.
    """

    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self._lock = Lock()
        self._version: int = 0            # monotonic; +1 per update

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
```

### The version counter

The SSE generator remembers the last version it sent and skips the tick when nothing moved. That
is what makes a 15-second Massive poll produce a 15-second SSE cadence without any coupling between
the two:

```python
last_version = -1
while True:
    if price_cache.version != last_version:
        last_version = price_cache.version
        yield format_sse(price_cache.get_all())
    await asyncio.sleep(0.5)
```

> **Known limitation.** `version` is global, not per-ticker: one ticker moving marks the whole
> snapshot dirty and the endpoint re-sends every ticker. Fine at 10–50 tickers (a full payload is
> ~2 KB). If per-ticker deltas are ever needed, add `dict[str, int]` versions — do **not** diff
> payloads client-side.

---

## 6. `events.py` — EventLog

Shocks are the most interesting thing the simulator does and are currently only `logger.debug`.
A drain-style queue would mean the first SSE client to poll consumes the event and the others never
see it. A bounded ring buffer with monotonic ids gives every client its own cursor.

```python
"""Bounded, multi-reader log of notable market events."""

from __future__ import annotations

from collections import deque
from threading import Lock

from .models import MarketEvent


class EventLog:
    """Append-only ring buffer of MarketEvents, read by cursor.

    Producers append; each SSE client keeps its own cursor and asks for
    everything since. Bounded, so a long-running server cannot grow unbounded
    and a client that reconnects after an outage simply skips ahead.
    """

    def __init__(self, capacity: int = 200) -> None:
        self._events: deque[tuple[int, MarketEvent]] = deque(maxlen=capacity)
        self._next_id: int = 0
        self._lock = Lock()

    def append(self, event: MarketEvent) -> None:
        with self._lock:
            self._events.append((self._next_id, event))
            self._next_id += 1

    def extend(self, events: list[MarketEvent]) -> None:
        for event in events:
            self.append(event)

    def since(self, cursor: int) -> tuple[int, list[MarketEvent]]:
        """Events with id >= cursor, plus the cursor to pass next time.

        Pass cursor=-1 on connect to start at 'now' and skip the backlog.
        """
        with self._lock:
            if cursor < 0:
                return self._next_id, []
            fresh = [event for event_id, event in self._events if event_id >= cursor]
            return self._next_id, fresh

    @property
    def cursor(self) -> int:
        with self._lock:
            return self._next_id
```

---

## 7. `interface.py` — MarketDataSource ABC

Five methods. That is the whole contract.

```python
"""Abstract interface for market data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod


class PermanentMarketDataError(RuntimeError):
    """A failure that retrying cannot fix — bad key, plan lacks entitlement.

    Raised by a source to tell the caller to stop polling and fail over,
    as opposed to a transient error which is logged and retried.
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
```

**Deliberate choices**

- **No `get_price()`.** Prices come from the cache, so no caller has to know whether a read costs a
  network hop.
- **Async mutators even though the simulator's are synchronous.** The Massive implementation may
  need I/O later; a uniform signature means no caller changes if a source does.
- **`get_tickers()` is sync** — local state only.
- **Contract, not inheritance.** No shared base implementation; the two sources have nothing in
  common beyond these five signatures.

---

## 8. `seed_prices.py` — constants

Constants only, no logic, so parameters can be tuned without touching the engine.

```python
"""Seed prices and per-ticker parameters for the market simulator."""

# Plausible-but-fixed starting prices. Deliberately not tracking reality — a
# simulator that pretends to be live data invites confusion about which mode is
# running. The order-of-magnitude spread ($175 → $800) exercises the portfolio
# weight and heatmap layout code far better than a uniform set would.
SEED_PRICES: dict[str, float] = {
    "AAPL": 190.00, "GOOGL": 175.00, "MSFT": 420.00, "AMZN": 185.00,
    "TSLA": 250.00, "NVDA": 800.00, "META": 500.00, "JPM": 195.00,
    "V": 280.00, "NFLX": 600.00,
}

# sigma: annualised volatility. mu: annualised drift.
TICKER_PARAMS: dict[str, dict[str, float]] = {
    "AAPL":  {"sigma": 0.22, "mu": 0.05},
    "GOOGL": {"sigma": 0.25, "mu": 0.05},
    "MSFT":  {"sigma": 0.20, "mu": 0.05},
    "AMZN":  {"sigma": 0.28, "mu": 0.05},
    "TSLA":  {"sigma": 0.50, "mu": 0.03},   # high vol, low drift
    "NVDA":  {"sigma": 0.40, "mu": 0.08},   # high vol, strong drift
    "META":  {"sigma": 0.30, "mu": 0.05},
    "JPM":   {"sigma": 0.18, "mu": 0.04},   # low vol (bank)
    "V":     {"sigma": 0.17, "mu": 0.04},   # low vol (payments)
    "NFLX":  {"sigma": 0.35, "mu": 0.05},
}

# Applied to tickers added at runtime (e.g. the AI adds PYPL).
# MUST be copied per ticker — dict(DEFAULT_PARAMS) — never shared by reference.
DEFAULT_PARAMS: dict[str, float] = {"sigma": 0.25, "mu": 0.05}

# Price range for an unknown ticker with no seed.
UNKNOWN_PRICE_RANGE: tuple[float, float] = (50.0, 300.0)

CORRELATION_GROUPS: dict[str, set[str]] = {
    "tech": {"AAPL", "GOOGL", "MSFT", "AMZN", "META", "NVDA", "NFLX"},
    "finance": {"JPM", "V"},
}

INTRA_TECH_CORR = 0.6       # tech stocks move together
INTRA_FINANCE_CORR = 0.5    # finance stocks move together
CROSS_GROUP_CORR = 0.3      # between sectors, and unknown tickers
TSLA_CORR = 0.3             # TSLA does its own thing
```

---

## 9. `simulator.py` — GBM engine + async source

Two classes, split by responsibility: **pure maths with no I/O**, wrapped in **an async loop with
no maths**.

### 9.1 The maths

Exact discretisation of Geometric Brownian Motion — the closed-form solution, so there is no Euler
discretisation error:

```
S(t+dt) = S(t) · exp( (μ − σ²/2)·dt  +  σ·√dt·Z )
```

`dt` is a fraction of a **trading** year, which is what makes `σ = 0.22` mean the same thing it
means on a real quote screen:

```python
TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600   # 5,896,800
DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR   # 8.479e-08
```

The `−σ²/2` Itô correction makes `μ` the drift of the *log* price, so `E[S(t)] = S(0)·e^{μt}`.
Dropping it gives every ticker an unintended upward bias that compounds (~13%/yr at σ=0.5).

Per-tick move at σ=0.22: `σ·√dt ≈ 0.0064%` — about 1.2 cents on a $190 stock. Sub-cent jitter that
accumulates into visible trends over minutes; exactly the texture a trading terminal should have.

### 9.2 Correlated draws

Independent draws look obviously fake when ten tickers wander in ten directions. Given correlation
matrix `C`, factor `C = L·Lᵀ` (Cholesky); for independent standard normals `z`, the vector `L·z`
has covariance `C` — correlated draws with unit variance preserved, so per-ticker σ still means
what it did.

The block structure below is positive-definite for **any** ticker count (measured minimum
eigenvalue +0.400 at n = 10, 47, 100). **Rule when tuning:** keep every off-diagonal ρ < 1 and keep
the rule a function of group membership only. Hand-editing individual pairs (ρ(AAPL,MSFT)=0.9 while
ρ(AAPL,NVDA)=0.1) can break PD and crash the next `add_ticker()`.

### 9.3 Shock events — calibration

The shipped `event_probability = 0.001` fires ~47 shocks per ticker per session, contributing
`√47 × 3.5% ≈ 24%` daily volatility and completely swamping σ: measured 1-day std is ~24.6% for
JPM, AAPL **and** TSLA alike, making `TICKER_PARAMS` dead config. Target **~1 shock per ticker per
session** instead:

```python
DEFAULT_EVENT_PROBABILITY = 2e-5   # ~1 event per ticker per 46,800-tick session
```

Measured at `2e-5`: AAPL 4.25%, TSLA 5.08% 1-day std — elevated versus pure GBM, still
recognisably equity-like, σ ordering preserved. Shocks are applied in **log space**
(`*= exp(±m)`) so up and down moves are mirror images; `*= (1 ± m)` compounds with a positive skew.

### 9.4 `GBMSimulator` — the engine

Fully synchronous and deterministic given injected RNGs, so it unit-tests without an event loop.

```python
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

DEFAULT_EVENT_PROBABILITY = 2e-5


class GBMSimulator:
    """Geometric Brownian Motion simulator for correlated stock prices.

        S(t+dt) = S(t) * exp((mu - sigma^2/2) * dt + sigma * sqrt(dt) * Z)

    Z is correlated across tickers via the Cholesky factor of a sector-based
    correlation matrix. State is one float per ticker, so step() stays cheap at
    2 Hz regardless of how long the process has been running.
    """

    TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600   # 5,896,800
    DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR   # ~8.48e-8

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
        self._session_open: dict[str, float] = {}     # serves as previous_close
        self._events: list[MarketEvent] = []
        self._cholesky: np.ndarray | None = None

        for ticker in tickers:
            self._add_ticker_internal(ticker)
        self._rebuild_cholesky()      # once, not once per ticker

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
        ticker = normalize_ticker(ticker)
        if ticker in self._prices:
            return
        self._add_ticker_internal(ticker)
        self._rebuild_cholesky()

    def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if ticker not in self._prices:
            return
        self._tickers.remove(ticker)
        del self._prices[ticker]
        del self._params[ticker]
        self._session_open.pop(ticker, None)
        self._rebuild_cholesky()

    def get_price(self, ticker: str) -> float | None:
        return self._prices.get(normalize_ticker(ticker))

    def get_previous_close(self, ticker: str) -> float | None:
        """The price this ticker opened the session at — the day-change baseline."""
        return self._session_open.get(normalize_ticker(ticker))

    def get_tickers(self) -> list[str]:
        """Public so SimulatorDataSource never reaches into _tickers."""
        return list(self._tickers)

    # --- Internals ---

    def _apply_shock(self, ticker: str) -> None:
        """A sudden 2-5% move. Log-space so up and down are mirror images."""
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
            self._cholesky = None      # cholesky of 1x1 is pointless, of 0x0 raises
            return

        corr = np.eye(n)
        for i in range(n):
            for j in range(i + 1, n):
                rho = self._pairwise_correlation(self._tickers[i], self._tickers[j])
                corr[i, j] = corr[j, i] = rho

        try:
            self._cholesky = np.linalg.cholesky(corr)
        except np.linalg.LinAlgError:
            # Cannot happen with the group-based rule (see §9.2), but a crash on
            # add_ticker() would be user-visible. Degrade to independent draws.
            logger.error("Correlation matrix not positive-definite — using independent draws")
            self._cholesky = None

    @staticmethod
    def _pairwise_correlation(t1: str, t2: str) -> float:
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
```

### 9.5 `SimulatorDataSource` — the async wrapper

```python
class SimulatorDataSource(MarketDataSource):
    """MarketDataSource backed by the GBM simulator.

    Runs a background asyncio task that steps the simulation every
    `update_interval` seconds and writes the results to the PriceCache.
    """

    def __init__(
        self,
        price_cache: PriceCache,
        update_interval: float = 0.5,
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
        self._sim = GBMSimulator(tickers=tickers, event_probability=self._event_prob)

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
                pass          # expected: we cancelled it
        self._task = None
        logger.info("Simulator stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if not self._sim:
            return
        self._sim.add_ticker(ticker)
        self._write(ticker)   # priceable at once, not after up to 500 ms
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
                    if self._event_log:
                        self._event_log.extend(self._sim.drain_events())
            except Exception:
                logger.exception("Simulator step failed")
            await asyncio.sleep(self._interval)
```

**Behaviours that matter**

- Cadence is decoupled from the SSE cadence; both are 500 ms today, and the cache's version counter
  means an SSE tick with no new data sends nothing.
- The loop steps **all** tickers each tick, so cost scales with the watchlist, not with the number
  of connected clients.
- `stop()` is safe before `start()` and idempotent — required by lifespan teardown ordering.

---

## 10. `massive_client.py` — REST poller

Polls the Full Market Snapshot endpoint for **all** watched tickers in a single request, so request
count stays flat as the watchlist grows. The SDK client is synchronous (urllib3), so every call goes
through `asyncio.to_thread`.

### 10.1 The three things that make this hard

1. **`LastTrade` has no `timestamp`.** It is `sip_timestamp`, in **nanoseconds**. The current code
   reads `snap.last_trade.timestamp / 1000.0`, which raises `AttributeError` on *every* snapshot;
   the surrounding handler logs at WARNING and skips, so the cache stays empty while the app looks
   healthy. Dividing ns by 1000 would give year ~53,000,000 anyway.
2. **`last_trade` is `None` unless the plan includes trades.** Starter returns aggregates only. A
   fallback ladder is mandatory, not defensive politeness.
3. **The free Basic tier excludes snapshots entirely** — a valid key is not the same as usable
   data. That is what §11's fail-fast check exists for.

### 10.2 Extraction helpers

Module-level functions, not methods — pure, and trivially testable against real SDK models.

```python
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
        if 1e9 < candidate < 4e9:      # ~2001 .. 2096
            return candidate
    return None


def extract_price(snap) -> float | None:
    """Freshest available price, degrading gracefully across plan tiers."""
    if snap.last_trade is not None and snap.last_trade.price:
        return snap.last_trade.price       # Developer+ : actual last trade
    if snap.min is not None and snap.min.close:
        return snap.min.close              # Starter    : latest minute bar
    if snap.day is not None and snap.day.close:
        return snap.day.close              # today's bar so far
    if snap.prev_day is not None and snap.prev_day.close:
        return snap.prev_day.close         # pre-open / stale fallback
    return None


def extract_timestamp(snap) -> float | None:
    """Epoch seconds for the price returned by extract_price()."""
    if snap.last_trade is not None:
        ts = to_epoch_seconds(snap.last_trade.sip_timestamp)   # NOT .timestamp
        if ts is not None:
            return ts
    return to_epoch_seconds(snap.updated)


def extract_previous_close(snap) -> float | None:
    """Previous session's close — the day-change baseline."""
    if snap.prev_day is not None and snap.prev_day.close:
        return snap.prev_day.close
    return None


_PERMANENT_MARKERS = (
    "401", "403", "unauthorized", "not authorized",
    "not entitled", "forbidden", "invalid api key",
)


def is_permanent_failure(exc: Exception) -> bool:
    """BadResponse is a single flat type whose message is the raw body, so the
    only way to distinguish 'bad key / no entitlement' from 'try again later'
    is to inspect the text. 429 and 5xx are deliberately NOT listed — those are
    transient and the SDK already retries them.
    """
    text = str(exc).lower()
    return any(marker in text for marker in _PERMANENT_MARKERS)
```

### 10.3 The source

```python
class MassiveDataSource(MarketDataSource):
    """MarketDataSource backed by the Massive (Polygon.io) REST API.

    Polls GET /v2/snapshot/locale/us/markets/stocks/tickers for every watched
    ticker in one call, then writes the results to the PriceCache.

    Poll interval by plan (see MASSIVE_API.md §2):
      Basic (free)        snapshots EXCLUDED — this source cannot work
      Starter / Developer 15-min delayed  → 15 s is plenty
      Advanced+           real-time       → 2-5 s
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
        self.market_status: str | None = None   # "open" | "closed" | "extended-hours"
        self.last_poll_at: float | None = None

    async def start(self, tickers: list[str]) -> None:
        # Normalised HERE too, not only in add/remove — a lower-case watchlist
        # row from SQLite would otherwise silently produce no data.
        self._tickers = [normalize_ticker(t) for t in tickers]
        self._client = RESTClient(
            api_key=self._api_key,
            connect_timeout=self._connect_timeout,   # tighter than the 10 s default,
            read_timeout=self._read_timeout,         # so a hung request can't outlive its interval
        )

        # First poll happens inline so the caller can decide whether this source
        # is usable before committing to it (see factory.start_market_data).
        # A permanent failure propagates; a transient one is swallowed and retried.
        await self._poll_once()
        await self._refresh_market_status()

        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info(
            "Massive poller started: %d tickers, %.1fs interval, market=%s",
            len(self._tickers), self._interval, self.market_status,
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
```

### 10.4 Failure behaviour

| Failure | Detected as | Behaviour |
|---|---|---|
| 401 invalid key | `is_permanent_failure` → `PermanentMarketDataError` | At `start()`: propagates → factory falls back to simulator. Mid-run: poller stops, callback fires |
| 403 plan lacks snapshots (Basic) | same | same |
| Authenticated but no usable prices | `len(cache) == 0` after `start()` | Factory raises and falls back |
| 429 rate limit | transient | SDK retries (3×, backoff 0.1); then logged, retried next interval |
| 5xx / timeout / DNS | transient | Logged at WARNING, retried next interval; cache keeps last known prices |
| One ticker missing from the response | per-snapshot | Warned and skipped; other tickers still processed |
| `last_trade` absent (Starter) | ladder | Falls through to `min.close` → `day.close` → `prev_day.close` |

---

## 11. `factory.py` — source selection and failover

The **only** module that reads the environment.

```python
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

    The env var is read here rather than left to the SDK, which sidesteps the
    SDK's import-time default-argument trap (MASSIVE_API.md §3).
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
```

Mid-run failover uses the `on_permanent_failure` hook, wired in the lifespan (§13) so the swap
reassigns `app.state.market_source` and every subsequent request picks up the new source.

---

## 12. `stream.py` — SSE endpoint

`GET /api/stream/prices`. One long-lived response per client; the browser's native `EventSource`
handles reconnection.

Three differences from the shipped version, all deliberate:
the `APIRouter` is built **inside** the factory (calling it twice previously registered `/prices`
twice on a module-level router — harmless in the app, a footgun in tests); a **heartbeat comment**
keeps idle connections alive through proxies; and **named events** carry shocks and market status
without changing the shape of the default price message.

```python
"""SSE streaming endpoint for live price updates."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator, Callable

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache
from .events import EventLog

logger = logging.getLogger(__name__)


def create_stream_router(
    price_cache: PriceCache,
    *,
    interval: float = 0.5,
    heartbeat: float = 15.0,
    event_log: EventLog | None = None,
    status_provider: Callable[[], str | None] | None = None,
) -> APIRouter:
    """Build the SSE router. The router is created here (not at module level) so
    calling this twice — in tests, say — yields two independent routers.
    """
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        """Live price stream.

            data: {"AAPL": {"ticker": "AAPL", "price": 190.50, ...}, ...}

        Plus two named event types: `shock` (a notable move) and `status`
        (market open/closed). Clients that ignore them still work.
        """
        return StreamingResponse(
            _generate_events(
                price_cache, request,
                interval=interval, heartbeat=heartbeat,
                event_log=event_log, status_provider=status_provider,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",   # defeat nginx response buffering
            },
        )

    return router


async def _generate_events(
    price_cache: PriceCache,
    request: Request,
    *,
    interval: float = 0.5,
    heartbeat: float = 15.0,
    event_log: EventLog | None = None,
    status_provider: Callable[[], str | None] | None = None,
) -> AsyncGenerator[str, None]:
    """Yield SSE frames until the client disconnects."""
    yield "retry: 1000\n\n"                       # EventSource reconnects after 1 s

    last_version = -1
    last_status: str | None = None
    cursor = event_log.cursor if event_log else 0  # start at 'now'; skip the backlog
    last_sent = time.monotonic()
    client_ip = request.client.host if request.client else "unknown"
    logger.info("SSE client connected: %s", client_ip)

    try:
        while True:
            if await request.is_disconnected():
                logger.info("SSE client disconnected: %s", client_ip)
                break

            sent = False

            # 1. Price snapshot — only when something actually moved.
            version = price_cache.version
            if version != last_version:
                last_version = version
                prices = price_cache.get_all()
                if prices:
                    payload = json.dumps({t: u.to_dict() for t, u in prices.items()})
                    yield f"data: {payload}\n\n"
                    sent = True

            # 2. Notable moves, per-client cursor so every client sees each one.
            if event_log is not None:
                cursor, fresh = event_log.since(cursor)
                for event in fresh:
                    yield f"event: shock\ndata: {json.dumps(event.to_dict())}\n\n"
                    sent = True

            # 3. Market status transitions (real data only; None under the simulator).
            if status_provider is not None:
                status = status_provider()
                if status != last_status:
                    last_status = status
                    yield f"event: status\ndata: {json.dumps({'market': status})}\n\n"
                    sent = True

            # 4. Comment-only heartbeat so idle proxies don't drop the connection.
            now = time.monotonic()
            if sent:
                last_sent = now
            elif now - last_sent >= heartbeat:
                yield ": keep-alive\n\n"
                last_sent = now

            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("SSE stream cancelled for: %s", client_ip)
```

### Wire format

```
retry: 1000

data: {"AAPL":{"ticker":"AAPL","price":190.5,"previous_price":190.42,"timestamp":1785000000.5,
       "change":0.08,"change_percent":0.042,"direction":"up","previous_close":190.0,
       "day_change":0.5,"day_change_percent":0.2632}, "GOOGL":{...}}

event: shock
data: {"ticker":"TSLA","magnitude_percent":-3.4,"price":241.5,"timestamp":1785000012.0}

: keep-alive
```

### Why poll-and-push rather than event-driven

The generator polls the cache on a fixed interval instead of being woken by the producer. It is
simpler (no pub/sub, no per-client queues, no backpressure handling) and produces evenly spaced
frames — which matters because the frontend accumulates them into sparklines, and irregular spacing
makes those look wrong.

---

## 13. FastAPI wiring and consumers

### 13.1 Lifespan

```python
# backend/app/main.py
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.market import (
    EventLog,
    MarketDataSource,
    PriceCache,
    create_stream_router,
    start_market_data,
)
from app.db import load_tracked_tickers      # union(watchlist, position tickers)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- startup ---
    # Synchronous: the database layer is plain sqlite3, and this runs before
    # anything is being served, so there is no event loop to stall. It is also
    # what lazily creates and seeds the database on first boot.
    tickers = load_tracked_tickers()
    source = await start_market_data(
        app.state.price_cache, tickers, event_log=app.state.event_log
    )
    source.on_permanent_failure = make_failover_handler(app)   # public since CP1
    app.state.market_source = source

    try:
        yield
    finally:
        # --- shutdown ---
        # Set before the first await, so a failover completing right now sees
        # it and retires its own replacement instead of leaving a task running
        # that nothing will ever stop.
        app.state.shutting_down = True
        current, app.state.market_source = app.state.market_source, None
        if current is not None:
            await current.stop()
```

The cache, the event log and the router are built in `create_app()` rather than in the lifespan, so
the routes exist before startup and each test gets its own instances:

```python
def create_app() -> FastAPI:
    price_cache, event_log = PriceCache(), EventLog()
    app = FastAPI(title="FinAlly", lifespan=lifespan)
    app.state.price_cache = price_cache
    app.state.event_log = event_log
    app.state.market_source = None
    app.state.shutting_down = False

    app.include_router(health_router)
    app.include_router(
        create_stream_router(
            price_cache,
            event_log=event_log,
            # Read through app.state, never captured: failover replaces the
            # source, and a captured reference reports the dead one forever.
            status_provider=lambda: current_market_status(app),
        )
    )
    mount_static(app)      # last: StaticFiles at "/" matches every path
    return app
```

The failover handler builds a simulator *specifically* — re-running source selection would read
`MASSIVE_API_KEY` and hand back the source that just died:

```python
async def on_permanent_failure(exc: Exception) -> None:
    failed = app.state.market_source
    tickers = failed.get_tickers() if failed is not None else []
    try:
        fallback = create_simulator_source(app.state.price_cache, event_log=app.state.event_log)
        fallback.on_permanent_failure = on_permanent_failure   # protect the replacement too
        await fallback.start(tickers)
    except Exception:
        # Invoked from an `except` block in the failed source's own task, so an
        # escape here shows up only as "Task exception was never retrieved".
        logger.exception("Failover failed; no market data source is running")
        app.state.market_source = None
        return

    if app.state.shutting_down:
        await fallback.stop()
        return

    app.state.market_source = fallback
    if failed is not None:
        await failed.stop()     # safe from in here — see §7 on on_permanent_failure
```

Handlers reach these through `Depends` providers in `app/api/deps.py`, which resolve the
`market_source is None` case (before startup, or after a failover that could not start a
replacement) exactly once rather than in each handler:

```python
def get_price_cache(request: Request) -> PriceCache:
    return request.app.state.price_cache


def get_market_source(request: Request) -> MarketDataSource:
    source = request.app.state.market_source
    if source is None:
        raise HTTPException(503, "market data unavailable")
    return source
```

### 13.2 Pricing a trade — never awaits I/O

```python
@router.post("/portfolio/trade")
async def execute_trade(
    trade: TradeRequest,
    price_cache: PriceCache = Depends(get_price_cache),
):
    ticker = normalize_ticker(trade.ticker)
    price = price_cache.get_price(ticker)
    if price is None:
        # A just-added ticker has no price for up to one poll interval (15 s on
        # Massive). Reject with a clear message — NEVER default to 0.
        raise HTTPException(400, f"Price not yet available for {ticker}. Try again in a moment.")

    fill_value = price * trade.quantity
    ...
```

### 13.3 Valuing the portfolio — tolerate missing tickers

```python
prices = price_cache.get_all()
total = cash + sum(
    position.quantity * prices[position.ticker].price
    for position in positions
    if position.ticker in prices          # a removed ticker must not silently zero the total
)
```

### 13.4 Watchlist coordination

Whoever mutates the DB watchlist also mutates the source, in that order, in the same handler.

The handler is `async def`, and the database call goes through `run_in_threadpool`: `app.db` is
synchronous, and a plain `def` handler — which would thread the query automatically — cannot
`await source.add_ticker()`. Splitting the two across handlers is not an option; a watchlist row
whose ticker was never added to the source is a row that never gets a price.

```python
from fastapi.concurrency import run_in_threadpool


@router.post("/watchlist")
async def add_to_watchlist(
    payload: WatchlistAdd,
    source: MarketDataSource = Depends(get_market_source),
    price_cache: PriceCache = Depends(get_price_cache),
):
    ticker = normalize_ticker(payload.ticker)
    await run_in_threadpool(db.add_watchlist_entry, ticker)
    await source.add_ticker(ticker)
    # Simulator: seeded synchronously, so a price is already available.
    # Massive: None until the next poll — the frontend renders "—".
    return {"ticker": ticker, "price": price_cache.get_price(ticker)}


@router.delete("/watchlist/{ticker}")
async def remove_from_watchlist(
    ticker: str,
    source: MarketDataSource = Depends(get_market_source),
):
    ticker = normalize_ticker(ticker)
    await run_in_threadpool(db.delete_watchlist_entry, ticker)

    # Positions outlive the watchlist. Keep tracking a ticker we still hold, or
    # portfolio valuation silently loses it.
    position = await run_in_threadpool(db.get_position, ticker)
    if position is None or position.quantity == 0:
        await source.remove_ticker(ticker)

    return {"status": "ok"}
```

**Tracked set = `union(watchlist, position_tickers)`** — that is what `load_tracked_tickers()`
returns at startup, and the rule the delete handler preserves.

---

## 14. Frontend consumption

```javascript
const source = new EventSource('/api/stream/prices');

// Default message: full snapshot, keyed by ticker.
source.onmessage = (e) => {
  const prices = JSON.parse(e.data);
  for (const [ticker, u] of Object.entries(prices)) {
    applyPrice(ticker, u.price, u.direction);        // flash green/red for ~500 ms
    setDayChange(ticker, u.day_change_percent);      // null → render "—"
    pushSparklinePoint(ticker, u.timestamp, u.price);
  }
};

// Notable moves — badge the ticker.
source.addEventListener('shock', (e) => {
  const { ticker, magnitude_percent } = JSON.parse(e.data);
  flagEvent(ticker, magnitude_percent);
});

// Market open/closed (real data only; null under the simulator).
source.addEventListener('status', (e) => {
  const { market } = JSON.parse(e.data);
  setMarketBanner(market);       // e.g. "Market closed — last close shown"
});

source.onerror = () => setConnectionDot('reconnecting');   // EventSource retries automatically
source.onopen  = () => setConnectionDot('connected');
```

`timestamp` is epoch **seconds** — multiply by 1000 before `new Date(...)`.

---

## 15. Error handling and edge cases

| Situation | Behaviour |
|---|---|
| **Empty watchlist at startup** | Both sources handle an empty list; SSE sends nothing until a ticker is added, then prices appear immediately (simulator) or on the next poll (Massive) |
| **Trade on an unpriced ticker** | HTTP 400 with a clear message. Never fill at 0 |
| **Ticker removed while a position is open** | Handler keeps tracking it; valuation additionally skips missing tickers as a belt-and-braces guard |
| **Invalid Massive key** | Detected at startup → logged and fell back to the simulator; app is fully functional with simulated prices |
| **Massive key on the free Basic plan** | `start()` succeeds but the cache is empty → `start_market_data` raises internally and falls back |
| **Massive dies mid-run** | Poller stops, `on_permanent_failure` hot-swaps to the simulator; prices resume within one tick |
| **Market closed (real data)** | Prices legitimately static; `event: status` tells the header to say so. Never synthesise movement onto real data |
| **SSE client disconnects** | `request.is_disconnected()` ends the generator; the task is garbage-collected |
| **Server shutdown with clients connected** | `CancelledError` is caught in the generator; `source.stop()` cancels the producer |
| **Simulator step raises** | Caught inside the loop, logged with traceback, loop continues |
| **Correlation matrix not PD** | Cannot happen with the group rule; if it ever did, falls back to independent draws instead of crashing `add_ticker()` |
| **Removing down to 0 or 1 tickers** | `_cholesky = None`; `step()` returns `{}` for 0 tickers without raising |
| **Unknown ticker added by the AI** | Random seed price in `[50, 300)` plus `DEFAULT_PARAMS` — works instantly, no lookup table, no failure path |
| **Float precision** | Prices round to 2dp on entry to the cache; GBM is exponential, so prices stay strictly positive |
| **Lock contention** | 10 tickers × 2 Hz against a dict-assignment critical section is negligible. A read/write lock would be premature |

---

## 16. Testing strategy

Current state: 73 tests, 84% coverage — but 13 passing Massive tests did not catch a **total**
failure of that integration, because `MagicMock` fabricates any attribute on access, so
`snap.last_trade.timestamp` "worked" in tests and raised in production. That is the lesson the test
design below is built around.

### 16.1 Fixtures from real SDK models — not MagicMock

```python
# tests/market/conftest.py
from massive.rest.models.snapshot import TickerSnapshot


def make_snapshot(
    ticker: str = "AAPL",
    price: float = 190.50,
    ts_ns: int = 1675190399000000000,
    with_trade: bool = True,
    with_min: bool = True,
    with_day: bool = True,
) -> TickerSnapshot:
    """Parse a documented payload through the SDK's own deserialiser, so the
    fixture has exactly the attributes the real API produces — and, crucially,
    NOT the ones it doesn't.
    """
    raw: dict = {
        "ticker": ticker,
        "todaysChange": -4.54,
        "todaysChangePerc": -3.50,
        "updated": ts_ns,
        "prevDay": {"o": 128.0, "h": 130.0, "l": 127.0, "c": 129.61, "v": 98_000_000},
    }
    if with_day:
        raw["day"] = {"o": 129.61, "h": 130.15, "l": 125.07, "c": price, "v": 111_237_700}
    if with_min:
        raw["min"] = {"av": 111_237_700, "o": 125.1, "h": 125.2, "l": 125.0,
                      "c": price, "t": 1675190340000}
    if with_trade:
        raw["lastTrade"] = {"p": price, "s": 100, "x": 4, "t": ts_ns, "c": [1]}
    return TickerSnapshot.from_dict(raw)
```

```python
# tests/market/test_massive.py
import pytest

from app.market.massive_client import (
    MassiveDataSource, extract_price, extract_timestamp, to_epoch_seconds,
)
from app.market.cache import PriceCache
from app.market.interface import PermanentMarketDataError


def test_price_from_last_trade():                      # Developer+
    assert extract_price(make_snapshot(price=190.5)) == 190.5


def test_price_falls_back_to_minute_bar():             # Starter: no trades entitlement
    snap = make_snapshot(price=188.25, with_trade=False)
    assert extract_price(snap) == 188.25


def test_price_falls_back_to_prev_day():               # pre-open
    snap = make_snapshot(with_trade=False, with_min=False, with_day=False)
    assert extract_price(snap) == 129.61


def test_no_lasttrade_timestamp_attribute():
    """The regression test for the bug that shipped: LastTrade has sip_timestamp."""
    snap = make_snapshot()
    assert not hasattr(snap.last_trade, "timestamp")


def test_timestamp_lands_in_a_sane_range():
    ts = extract_timestamp(make_snapshot(ts_ns=1675190399000000000))
    assert 1.6e9 < ts < 1.8e9                          # ns/1e3 would be year ~53,000,000


@pytest.mark.parametrize("raw", [1675190399, 1675190399000, 1675190399000000,
                                 1675190399000000000])
def test_epoch_normalisation_by_magnitude(raw):
    assert 1.6e9 < to_epoch_seconds(raw) < 1.8e9


@pytest.mark.asyncio
async def test_poll_populates_cache_with_previous_close():
    cache = PriceCache()
    source = MassiveDataSource(api_key="k", price_cache=cache, poll_interval=60)
    source._tickers = ["AAPL"]
    source._client = object()                          # only truthiness is checked
    source._fetch_snapshots = lambda: [make_snapshot(price=190.5)]

    assert await source._poll_once() == 1
    update = cache.get("AAPL")
    assert update.price == 190.5
    assert update.previous_close == 129.61
    assert update.day_change_percent is not None


@pytest.mark.asyncio
async def test_transient_error_keeps_previous_prices():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    source = MassiveDataSource(api_key="k", price_cache=cache, poll_interval=60)
    source._tickers = ["AAPL"]
    source._client = object()
    source._fetch_snapshots = lambda: (_ for _ in ()).throw(Exception("503 upstream"))

    assert await source._poll_once() == 0              # does not raise
    assert cache.get_price("AAPL") == 190.0            # last known price retained


@pytest.mark.asyncio
async def test_permanent_error_raises():
    cache = PriceCache()
    source = MassiveDataSource(api_key="bad", price_cache=cache, poll_interval=60)
    source._tickers = ["AAPL"]
    source._client = object()
    source._fetch_snapshots = lambda: (_ for _ in ()).throw(
        Exception("401 Unauthorized: invalid API key")
    )

    with pytest.raises(PermanentMarketDataError):
        await source._poll_once()
```

### 16.2 Contract tests — run against both sources

The strongest guard against the two implementations drifting apart. Add a third provider later and
it inherits the whole suite.

```python
# tests/market/test_source_contract.py
import pytest

from app.market.cache import PriceCache
from app.market.simulator import SimulatorDataSource


def make_massive(cache):
    source = MassiveDataSource(api_key="k", price_cache=cache, poll_interval=60)
    source._client = object()
    source._fetch_snapshots = lambda: [make_snapshot(t) for t in source._tickers]
    return source


@pytest.fixture(params=["simulator", "massive"])
def source_factory(request):
    return {
        "simulator": lambda cache: SimulatorDataSource(cache, update_interval=0.05),
        "massive": make_massive,
    }[request.param]


@pytest.mark.asyncio
async def test_start_populates_cache(source_factory):
    cache = PriceCache()
    source = source_factory(cache)
    await source.start(["AAPL", "GOOGL"])
    assert cache.get("AAPL") is not None               # priced before start() returns
    await source.stop()


@pytest.mark.asyncio
async def test_tickers_are_normalised(source_factory):
    cache = PriceCache()
    source = source_factory(cache)
    await source.start([" aapl "])
    assert source.get_tickers() == ["AAPL"]
    await source.stop()


@pytest.mark.asyncio
async def test_stop_is_idempotent_and_safe_before_start(source_factory):
    source = source_factory(PriceCache())
    await source.stop()                                # never started
    await source.start(["AAPL"])
    await source.stop()
    await source.stop()


@pytest.mark.asyncio
async def test_remove_drops_from_cache_and_set(source_factory):
    cache = PriceCache()
    source = source_factory(cache)
    await source.start(["AAPL", "GOOGL"])
    await source.remove_ticker("GOOGL")
    assert "GOOGL" not in source.get_tickers()
    assert cache.get("GOOGL") is None
    await source.stop()
```

### 16.3 Simulator properties worth asserting

| Property | Catches |
|---|---|
| Prices stay > 0 over 10,000+ steps | A broken multiplicative invariant; a negative price corrupts portfolio maths |
| `step()` returns exactly the tracked tickers | Cholesky/ticker-list desync |
| Same seeded RNG ⇒ identical price paths | Determinism for E2E and screenshots |
| 1-day std ≈ `σ/√252` with shocks disabled | A dropped `√dt` or Itô term |
| Shock rate ≈ 1/ticker/session at `2e-5` | Silent recalibration of the shock process |
| Shocks are symmetric in log space | Reintroduction of `*= (1 ± m)` skew |
| `len(_tickers) == cholesky.shape[0]` after add/remove churn | The invariant behind correlated draws |
| Cholesky succeeds at n = 1, 2, 10, 100 | The PD guarantee |
| Empirical pairwise correlation ≈ configured ρ | **The only test that proves Cholesky is applied at all** |
| Unknown ticker gets a price in `[50, 300)` | The AI-adds-a-ticker path |

```python
def test_correlation_is_actually_applied():
    """Without this, dropping the Cholesky multiply passes every other test."""
    import numpy as np

    sim = GBMSimulator(["AAPL", "MSFT"], rng=np.random.default_rng(42))
    paths = {"AAPL": [], "MSFT": []}
    prev = dict(AAPL=sim.get_price("AAPL"), MSFT=sim.get_price("MSFT"))
    for _ in range(20_000):
        prices = sim.step()
        for t in paths:
            paths[t].append(math.log(prices[t] / prev[t]))
            prev[t] = prices[t]

    rho = np.corrcoef(paths["AAPL"], paths["MSFT"])[0, 1]
    assert 0.5 < rho < 0.7          # configured INTRA_TECH_CORR = 0.6


def test_shock_rate_matches_configuration():
    sim = GBMSimulator(["AAPL"], event_probability=2e-5,
                       py_rng=random.Random(7), rng=np.random.default_rng(7))
    for _ in range(46_800):         # one 6.5h session
        sim.step()
    assert 0 <= len(sim.drain_events()) <= 5      # expectation ~1
```

### 16.4 SSE integration test

> **Corrected during Checkpoint 1.** The `httpx.ASGITransport` approach below **does not work** and
> was not shipped. The SSE generator is infinite by design, and ASGITransport never delivers an
> `http.disconnect` message, so `request.is_disconnected()` never returns True and closing the
> response blocks forever — verified: the client hangs before receiving even the first frame.
>
> The shipped `tests/market/test_stream.py` instead drives `_generate_events` directly with a stub
> request that reports disconnected after N loop iterations, and asserts the HTTP wiring (route
> path, media type, headers) off the router object. That is deterministic, needs no sleeps, and
> takes `stream.py` from 31% to 97%. The code below is retained only to record what was tried.

`stream.py` sat at 31% coverage with no dedicated tests, despite being the primary consumer of the
cache. An ASGI transport tests it without a real server:

```python
# tests/market/test_stream.py
import json

import httpx
import pytest
from fastapi import FastAPI

from app.market import EventLog, PriceCache, create_stream_router
from app.market.models import MarketEvent


@pytest.mark.asyncio
async def test_stream_emits_snapshot_then_shock():
    cache = PriceCache()
    events = EventLog()
    cache.update("AAPL", 190.0)

    app = FastAPI()
    app.include_router(create_stream_router(cache, interval=0.01, event_log=events))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("GET", "/api/stream/prices") as response:
            assert response.headers["content-type"].startswith("text/event-stream")
            frames = []
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    frames.append(json.loads(line[6:]))
                    if len(frames) == 1:
                        events.append(MarketEvent("TSLA", -3.4, 241.5))
                    if len(frames) == 2:
                        break

    assert frames[0]["AAPL"]["price"] == 190.0
    assert frames[1]["ticker"] == "TSLA"


@pytest.mark.asyncio
async def test_unchanged_cache_sends_nothing_new():
    """The version counter is what keeps a 15 s Massive poll from producing
    30 identical SSE frames."""
    ...
```

### 16.5 Cache concurrency

```python
def test_concurrent_writers_and_readers():
    from concurrent.futures import ThreadPoolExecutor

    cache = PriceCache()

    def write(n):
        for i in range(1000):
            cache.update(f"T{n}", 100.0 + i * 0.01)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(8)))

    assert len(cache) == 8
    assert cache.version == 8000
```

### 16.6 Coverage targets

| Module | Target | Note |
|---|---|---|
| models, cache, events, interface, seed_prices, factory | 100% | Pure logic, no excuses |
| simulator | ≥ 95% | Loop exception path via an injected failing sim |
| massive_client | ≥ 85% | Extraction helpers fully covered by real-model fixtures |
| stream | ≥ 80% | Via the ASGI transport test above |

---

## 17. Delta from the current implementation

Everything below is a change relative to `backend/app/market/` as it stands. Ordered by
consequence.

| # | Change | File | Why |
|---|---|---|---|
| 1 | Replace `snap.last_trade.price` / `.timestamp / 1000.0` with the `extract_price` / `extract_timestamp` ladder | `massive_client.py` | **The Massive integration cannot populate the cache today.** `LastTrade.timestamp` does not exist, so every snapshot raises `AttributeError`, is caught, and skipped — the cache stays empty while the app looks healthy |
| 2 | Rebuild the Massive test fixtures from `TickerSnapshot.from_dict(...)` | `tests/market/test_massive.py` | `MagicMock` fabricates any attribute, which is why 13 passing tests missed #1 entirely |
| 3 | `event_probability` `0.001` → `2e-5`, shocks applied in log space | `simulator.py` | At `0.001` daily volatility is ~17× too high and identical (~24.6%) for every ticker, making `TICKER_PARAMS` dead config and portfolio P&L meaningless |
| 4 | Add `start_market_data()` — fail fast, then fall back to the simulator | `factory.py` | Turns the worst failure mode (valid free-tier key ⇒ permanently empty UI, no error) into a logged fallback with a working app |
| 5 | Classify permanent (401/403) vs transient failures; stop polling and fail over on permanent | `massive_client.py` | A broad `except Exception` retries a dead key every 15 s forever with no signal |
| 6 | Normalise tickers in `start()` too, via a shared `normalize_ticker()` | all | Massive tickers are case-sensitive; a lower-case DB row silently yields no data |
| 7 | Add `previous_close` to `PriceUpdate` + `day_change_percent`; both sources supply it | `models.py`, `cache.py`, both sources | The watchlist's "daily change %" is currently either tick-over-tick (wrong) or frontend-derived and reset on refresh |
| 8 | Add `MarketEvent` + `EventLog`; publish shocks over SSE as `event: shock` | new `events.py`, `simulator.py`, `stream.py` | The most interesting thing the simulator does is currently `logger.debug` only |
| 9 | Build the `APIRouter` inside `create_stream_router` | `stream.py` | The module-level router registers `/prices` twice if the factory is called twice |
| 10 | Add SSE heartbeat comments | `stream.py` | Idle connections through a proxy can be dropped without one |
| 11 | `RESTClient(connect_timeout=5, read_timeout=5)` | `massive_client.py` | The 10 s default lets a hung request outlive its 15 s poll interval |
| 12 | Poll `get_market_status()`; expose it as `event: status` | `massive_client.py`, `stream.py` | With real data, prices are static outside 09:30–16:00 ET and the UI looks broken |
| 13 | `timestamp if timestamp is not None` instead of `timestamp or time.time()` | `cache.py` | `0.0` is falsy |
| 14 | Read `version` under the lock | `cache.py` | Consistency with the rest of the class; correct under a free-threaded build |
| 15 | Inject RNGs into `GBMSimulator` | `simulator.py` | Deterministic statistical tests without seeding process-global RNGs |
| 16 | `MASSIVE_POLL_INTERVAL`, `SIM_EVENT_PROBABILITY`, `SIM_UPDATE_INTERVAL` env vars | `factory.py` | 15 s is right for Starter/Developer; Advanced+ should poll at 2–5 s |
| 17 | Add contract tests, an SSE integration test, and a cache concurrency test | `tests/market/` | The three untested seams, one of which hid #1 |

### Adding a third source later

The design keeps this to two files: implement `MarketDataSource` in
`app/market/<provider>_client.py` (normalise timestamps to epoch seconds, write only via
`cache.update()`), then add the branch to `create_market_data_source()`. It inherits the §16.2
contract tests unchanged. Nothing else in the codebase changes — which is the point.
