# Backend — Developer Guide

## Project Setup

```bash
cd backend
uv sync --extra dev   # Install all dependencies including test/lint tools
```

## Market Data API

The market data subsystem lives in `app/market/`. Use these imports:

```python
from app.market import (
    EventLog, MarketDataSource, MarketEvent, PermanentMarketDataError,
    PriceCache, PriceUpdate, create_stream_router, normalize_ticker,
    start_market_data,
)
```

Import from `app.market` only — never from a submodule. `__init__.py` is the supported contract.

### Core Types

- **`PriceUpdate`** — Immutable dataclass: `ticker`, `price`, `previous_price`, `timestamp`,
  `previous_close`. Two distinct notions of change, do not conflate them:
  - `change` / `change_percent` / `direction` are **tick-over-tick** — they exist to flash a cell
    green or red for 500 ms
  - `day_change` / `day_change_percent` are **session-over-session** — the watchlist's "daily
    change %" column. `None` when `previous_close` is unknown; render an em dash, do not invent a
    baseline

- **`normalize_ticker(t)`** — upper-case and strip. Apply at every entry point (REST handlers, LLM
  tool calls). Massive tickers are case-sensitive; a lower-case DB row silently yields no data.

- **`PriceCache`** — Thread-safe in-memory store. Key methods:
  - `update(ticker, price, timestamp=None, previous_close=None) -> PriceUpdate`
    (`previous_close` is sticky — pass it once and later updates carry it forward)
  - `get(ticker) -> PriceUpdate | None`
  - `get_price(ticker) -> float | None`
  - `get_all() -> dict[str, PriceUpdate]`
  - `remove(ticker)`
  - `version` property — monotonic counter, increments on every update (for SSE change detection)

- **`MarketDataSource`** — Abstract interface implemented by `SimulatorDataSource` and
  `MassiveDataSource`. Lifecycle: `start(tickers)` -> `add_ticker()` / `remove_ticker()` -> `stop()`.
  Two public attributes every source carries, so consumers never need `getattr` or `isinstance`:
  - `market_status` — `"open"`/`"closed"`/`"extended-hours"`, or `None` where the concept does not
    apply (the simulator always trades)
  - `on_permanent_failure` — assign an async callback after construction to be told when a source
    hits a failure retrying cannot fix, so the app can swap in a working one mid-session

  `start()` does **not** promise any ticker got a price — a transient fetch failure is worth
  retrying, not aborting. Verify by reading the cache; `start_market_data` already does.

- **`EventLog`** — Bounded ring buffer of `MarketEvent`s (simulator shocks). Read by cursor:
  `since(cursor) -> (next_cursor, events)`, so every SSE client sees every event. Pass `cursor=-1`
  on connect to skip the backlog.

- **`start_market_data(cache, tickers, event_log=None)`** — Use this, not the bare factory. Creates
  and starts a source, and falls back to the simulator when Massive is unusable (bad key, or a
  valid key on a plan without snapshot entitlement, which otherwise yields a permanently empty UI
  with no error). `create_market_data_source(cache)` remains available when you need it unstarted.

### SSE Streaming

```python
router = create_stream_router(
    price_cache,
    event_log=event_log,                           # optional: publishes `event: shock`
    status_provider=lambda: source.market_status,  # optional: publishes `event: status`
)
# Endpoint: GET /api/stream/prices (text/event-stream)
```

Frames: a default `data:` price snapshot (only when the cache version changed), named `shock` and
`status` events, and `: keep-alive` comments every 15 s idle so proxies don't drop the connection.
The router is built inside the factory, so calling it twice yields two independent routers.

### Seed Data

Default tickers: AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX. Seed prices and
per-ticker volatility/drift params are in `app/market/seed_prices.py`.

## Environment variables

| Var | Default | Meaning |
|---|---|---|
| `MASSIVE_API_KEY` | *(empty)* | Non-empty after `.strip()` selects Massive; else the simulator |
| `MASSIVE_POLL_INTERVAL` | `15` | Seconds between polls (2-5 on Advanced+) |
| `SIM_UPDATE_INTERVAL` | `0.5` | Simulator tick, seconds. `dt` is derived from it, so `sigma` stays annualised volatility at any rate |
| `SIM_EVENT_PROBABILITY` | `2e-5` | Shock chance per ticker **per tick** — so the per-hour shock rate scales with `SIM_UPDATE_INTERVAL`; the ~1-per-session figure assumes the 0.5 s default |

`factory.py` is the only module that reads the environment. Blank or malformed
numeric values fall back to the default with a warning rather than crashing startup.

## Testing rules for this subsystem

Never build Massive fixtures with `MagicMock` — it fabricates any attribute on access, which is how
a client that could never populate the cache passed thirteen tests. Use
`tests/market/conftest.make_snapshot()`, which parses a documented payload through
`TickerSnapshot.from_dict`. New sources inherit `tests/market/test_source_contract.py` unchanged.

## Running Tests

```bash
uv run --extra dev pytest -v              # All tests
uv run --extra dev pytest --cov=app       # With coverage
uv run --extra dev ruff check app/ tests/ # Lint
```

## Demo

```bash
uv run market_data_demo.py   # Live terminal dashboard with simulated prices
```
