# Market Data Backend — Summary

**Status:** Hardened in Checkpoint 1 (see `PLAN.md` §13). All three gates passed — 228 tests, 100%
coverage on `app/market/`.
**Gate 2 code review is still outstanding**, so the checkpoint is not closed.

> This document previously read "Complete, tested, reviewed, all issues resolved" while
> `massive_client.py` could not populate the cache at all: it read `snap.last_trade.timestamp`,
> which does not exist on the SDK model, so every snapshot raised `AttributeError`, was swallowed,
> and was skipped. Thirteen `MagicMock`-based tests passed throughout, because a MagicMock
> fabricates any attribute on access. That is why fixtures are now built from
> `TickerSnapshot.from_dict`, and why claims in this file are tied to a verified gate.

## What Was Built

A market data subsystem in `backend/app/market/` (9 modules) providing live price simulation and real market data via a unified interface.

### Architecture

```
MarketDataSource (ABC)
├── SimulatorDataSource  →  GBM simulator (default, no API key needed)
└── MassiveDataSource    →  Polygon.io REST poller (when MASSIVE_API_KEY set)
        │
        ▼
   PriceCache (thread-safe, in-memory)
        │
        ├──→ SSE stream endpoint (/api/stream/prices)
        ├──→ Portfolio valuation
        └──→ Trade execution
```

### Modules

| File | Purpose |
|------|---------|
| `models.py` | `PriceUpdate` (adds `previous_close`, `day_change`, `day_change_percent`), `MarketEvent`, `normalize_ticker()` |
| `interface.py` | `MarketDataSource` ABC + `PermanentMarketDataError` |
| `events.py` | `EventLog` — bounded ring buffer of `MarketEvent`s with per-client cursors |
| `cache.py` | `PriceCache` — thread-safe price store with version counter for SSE change detection |
| `seed_prices.py` | Realistic seed prices, per-ticker GBM params (drift/volatility), correlation groups |
| `simulator.py` | `GBMSimulator` (Geometric Brownian Motion with Cholesky-correlated moves) + `SimulatorDataSource` |
| `massive_client.py` | `MassiveDataSource` — REST polling client for Polygon.io via the `massive` package |
| `factory.py` | `create_market_data_source()` selects the source; `start_market_data()` starts it and falls back to the simulator if Massive is unusable |
| `stream.py` | `create_stream_router()` — SSE endpoint; version-based change detection, heartbeats, `shock` and `status` events |

### Key Design Decisions

- **Strategy pattern** — both data sources implement the same ABC; downstream code is source-agnostic
- **PriceCache as single point of truth** — producers write, consumers read; no direct coupling
- **GBM with correlated moves** — Cholesky decomposition of sector-based correlation matrix; tech stocks correlate at 0.6, finance at 0.5, cross-sector at 0.3
- **Random shock events** — `2e-5` chance per tick per ticker of a 2-5% move, applied in log
  space so up and down are mirror images. Calibrated to ~1 shock per ticker per session; at the
  original 0.001 the shock process contributed ~24% daily volatility to every ticker alike and
  made `TICKER_PARAMS` dead config
- **SSE over WebSockets** — simpler, one-way push, universal browser support

## Test Suite

**228 tests, all passing** in ~2 s, with no network access. 9 test modules in
`backend/tests/market/`.

| Module | Coverage of |
|--------|-------------|
| test_models.py | models.py: 100% |
| test_cache.py | cache.py: 100% |
| test_events.py | events.py: 100% |
| test_simulator.py | simulator.py: 100% |
| test_simulator_source.py | (integration) |
| test_source_contract.py | both sources against one contract |
| test_massive.py | massive_client.py: 100% |
| test_stream.py | stream.py: 100% |
| test_factory.py | factory.py: 100% |

Overall coverage: 100% on `app/market/`. Treat that as a floor to hold, not proof of thoroughness —
line coverage says every line ran, not that its behaviour is pinned. Several of these tests only
became meaningful after mutation testing showed an earlier version passing against broken code.

Fixtures are built from `TickerSnapshot.from_dict(...)`, never `MagicMock` — see the note at the
top of this file for why.

## Review history

An earlier review of this subsystem resolved 7 issues (build config, lazy imports, SSE return type,
a public `get_tickers()`, correlation constants, unused test imports, Massive test mocks). It did
not catch the `last_trade.timestamp` defect, because the tests it was reviewing used `MagicMock`.

Checkpoint 1's Gate 2 review has **not yet been run**.

## Demo

A Rich terminal demo is available at `backend/market_data_demo.py`:

```bash
cd backend
uv run market_data_demo.py
```

Displays a live-updating dashboard with all 10 tickers, sparklines, color-coded direction arrows, and an event log for notable price moves. Runs 60 seconds or until Ctrl+C.

## Usage for Downstream Code

```python
from app.market import EventLog, PriceCache, start_market_data

# Startup — creates, starts, and falls back to the simulator if Massive is unusable
cache = PriceCache()
events = EventLog()
source = await start_market_data(cache, ["AAPL", "GOOGL", "MSFT", ...], event_log=events)

# Read prices
update = cache.get("AAPL")          # PriceUpdate or None
price = cache.get_price("AAPL")     # float or None
all_prices = cache.get_all()        # dict[str, PriceUpdate]

# Dynamic watchlist
await source.add_ticker("TSLA")
await source.remove_ticker("GOOGL")

# Mid-session failover: a source that hits a failure retrying cannot fix calls
# this, so the app can swap in a working one. Wired in app/main.py's lifespan
# since Checkpoint 2. Calling stop() on the failed source from inside the
# callback is safe — sources release their task before invoking it.
source.on_permanent_failure = handler

# Shutdown
await source.stop()
```

**Consumed by `app/main.py` since Checkpoint 2**, which owns the one `PriceCache` and `EventLog`,
starts the source from `load_tracked_tickers()`, and mounts `create_stream_router` with
`status_provider=lambda: current_market_status(app)`.
