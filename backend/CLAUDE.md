# Backend — Developer Guide

## Project Setup

```bash
cd backend
uv sync --extra dev   # Install all dependencies including test/lint tools
```

## Application wiring

`app/main.py` owns the app. `create_app()` builds one instance — its own
`PriceCache` and `EventLog` — and the lifespan starts the market feed from the
database's tracked tickers, wires `on_permanent_failure` so a dying Massive key
hot-swaps to the simulator, and stops the source on shutdown.

Reach the live objects through `request.app.state`, never a module global:

```python
@router.post("/portfolio/trade")
def execute_trade(payload: TradeRequest, request: Request):
    price_cache = request.app.state.price_cache
    source = request.app.state.market_source   # replaced on failover — re-read it
```

`market_source` is `None` before startup and after shutdown. Handlers that
mutate the watchlist must call `source.add_ticker()` / `remove_ticker()` in the
same handler as the database write (MARKET_DATA_DESIGN.md §13.4).

Static files are mounted at `/` **after** every router. `StaticFiles` at the
root matches every path, so mounting it earlier would swallow `/api/*`.

## Database API

```python
from app.db import connect, transaction, load_tracked_tickers, utc_now
```

Import from `app.db` only, the same contract `app.market` keeps.

- **`connect()`** — context manager yielding an initialised `sqlite3.Connection`
  with `row_factory = sqlite3.Row`. Autocommit: each statement commits alone
- **`transaction()`** — `connect()` wrapped in `BEGIN IMMEDIATE`. Use it for
  anything that must land atomically — a trade touches `positions`, `trades`,
  `users_profile` and `portfolio_snapshots` and must not land partially
- **`load_tracked_tickers()`** — `union(watchlist, positions)`. Positions are in
  the union deliberately: a ticker removed from the watchlist while still held
  must keep being priced or the portfolio total silently loses it
- **`utc_now()`** — the ISO-8601 string every `*_at` column stores

One connection per operation, opened and closed inside the helper. Write route
handlers as `def`, not `async def`, so FastAPI runs the blocking query in a
worker thread instead of stalling the event loop that drives the simulator tick
and every open SSE stream.

### Lazy initialisation

There is no migration step. Every `connect()` checks `sqlite_master` for the six
tables of PLAN.md §7 and, if any are missing, runs `schema.sql` and seeds the
default profile ($10,000) and the ten default tickers — under a lock, in one
transaction, with `IF NOT EXISTS` / `INSERT OR IGNORE` throughout.

The check is **per connection, never cached in a module flag**. A flag would let
the process believe a database it created still exists, and serve `no such
table` for the rest of its life once the file was deleted underneath it.

Seeding only runs when tables are missing, so it cannot resurrect a ticker the
user deleted.

Adding a table means adding it to `schema.sql` **and** to `REQUIRED_TABLES` — a
table absent from that tuple is not part of the "is it initialised?" test, so a
database missing only that table looks healthy and fails at query time.

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
| `DB_PATH` | `<repo>/db/finally.db` | SQLite file. Read on every call, not at import, so tests can redirect it. The container sets it to `/app/db/finally.db` |
| `STATIC_DIR` | `backend/static`, then `frontend/out` | Built frontend. Absent is normal — the API serves on its own |
| `LOG_LEVEL` | `INFO` | Root log level |
| `MASSIVE_API_KEY` | *(empty)* | Non-empty after `.strip()` selects Massive; else the simulator |
| `MASSIVE_POLL_INTERVAL` | `15` | Seconds between polls (2-5 on Advanced+) |
| `SIM_UPDATE_INTERVAL` | `0.5` | Simulator tick, seconds. `dt` is derived from it, so `sigma` stays annualised volatility at any rate |
| `SIM_EVENT_PROBABILITY` | `2e-5` | Shock chance per ticker **per tick** — so the per-hour shock rate scales with `SIM_UPDATE_INTERVAL`; the ~1-per-session figure assumes the 0.5 s default |

Within the market subsystem, `factory.py` is the only module that reads the
environment. Blank or malformed numeric values fall back to the default with a
warning rather than crashing startup.

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
