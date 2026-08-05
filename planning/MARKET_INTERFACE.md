# Market Data Interface — Unified Design

The single Python API FinAlly uses to retrieve stock prices. One abstract interface, two implementations: the **Massive API** when `MASSIVE_API_KEY` is set, the **GBM simulator** otherwise. Everything downstream — SSE streaming, portfolio valuation, trade execution, LLM context — is source-agnostic.

- Massive endpoint/field details: `MASSIVE_API.md`
- Simulator internals: `MARKET_SIMULATOR.md`
- Status of the built subsystem: `MARKET_DATA_SUMMARY.md`

This document describes the **as-built** design in `backend/app/market/` plus the corrections §7 requires before a real API key can work.

---

## 1. Design principles

| Principle | Consequence |
|---|---|
| **Push, don't pull** | Sources write into a shared cache on their own schedule. Callers never await a network round-trip to price a trade. |
| **The cache is the contract** | `PriceCache` is the only thing downstream code touches. Swapping sources changes nothing above it. |
| **One shape of data** | Every source produces `PriceUpdate`. No provider types leak past `massive_client.py`. |
| **Degrade, never crash** | A failed poll logs and retries; the app keeps serving the last known price. Missing key → simulator, not an error. |
| **Multi-user ready** | A single background producer serving N readers already matches a multi-user model; no data-layer change needed later. |

```
          ┌──────────────────────┐
          │ create_market_data_  │   reads MASSIVE_API_KEY
          │      source()        │
          └──────────┬───────────┘
            ┌────────┴────────┐
            ▼                 ▼
  SimulatorDataSource   MassiveDataSource
   (GBM, 500ms tick)     (REST poll, 15s)
            └────────┬────────┘
                     ▼  writes
              ┌─────────────┐
              │ PriceCache  │  thread-safe, in-memory, versioned
              └──────┬──────┘
                     │  reads
      ┌──────────────┼──────────────┬────────────────┐
      ▼              ▼              ▼                ▼
 SSE /api/stream  portfolio     trade exec      LLM context
   /prices        valuation    (fill price)     (chat prompt)
```

---

## 2. `PriceUpdate` — the only data structure that leaves the layer

`app/market/models.py`. Immutable (`frozen=True, slots=True`); derived values are properties, so they can never disagree with the prices they come from.

```python
@dataclass(frozen=True, slots=True)
class PriceUpdate:
    ticker: str
    price: float
    previous_price: float
    timestamp: float = field(default_factory=time.time)   # epoch SECONDS

    @property
    def change(self) -> float: ...          # price - previous_price, 4dp
    @property
    def change_percent(self) -> float: ...  # 0.0 when previous_price == 0
    @property
    def direction(self) -> str: ...         # "up" | "down" | "flat"

    def to_dict(self) -> dict: ...          # JSON/SSE payload
```

Conventions that the rest of the app relies on:

- **`timestamp` is always epoch seconds (float).** Every source normalises into this unit; nanosecond/millisecond conversion is a source's private problem (see `MASSIVE_API.md` §5).
- **`previous_price` is the previous *tick*, not the previous session's close.** It drives the green/red flash animation. Day-change % for the watchlist is a separate concept — see §6.
- **First update for a ticker sets `previous_price == price`**, so `direction == "flat"` and nothing flashes on page load.
- Prices are rounded to 2dp on entry to the cache; `change`/`change_percent` to 4dp.

---

## 3. `MarketDataSource` — the abstract interface

`app/market/interface.py`. Five methods, all that a source must provide.

```python
class MarketDataSource(ABC):
    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Begin producing updates. Call exactly once; twice is undefined."""

    @abstractmethod
    async def stop(self) -> None:
        """Cancel background work and release resources. Idempotent."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Add to the active set. No-op if present."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Remove from the active set and drop it from the cache. No-op if absent."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Currently tracked tickers."""
```

Deliberate choices:

- **No `get_price()` on the interface.** Prices are read from the cache. Otherwise every caller would need to care whether a read costs a network hop.
- **`async` mutators even though the simulator's are synchronous.** The Massive implementation may need I/O; a uniform async signature means callers never change if the source does.
- **`get_tickers()` is sync** — it reads local state only.
- **Contract, not inheritance.** No shared base implementation; the two sources have nothing meaningful in common beyond the five signatures.

### Lifecycle

```python
# FastAPI lifespan startup
cache = PriceCache()
source = create_market_data_source(cache)
await source.start(watchlist_tickers_from_db())

# watchlist changes (REST or LLM tool call)
await source.add_ticker("PYPL")
await source.remove_ticker("GOOGL")

# lifespan shutdown
await source.stop()
```

Both sources must be safe to `stop()` when never started, and `stop()` must swallow the `CancelledError` from cancelling its own task (both currently do).

---

## 4. `PriceCache` — shared state

`app/market/cache.py`. Single writer, many readers, guarded by a `threading.Lock` (not an asyncio lock, because `MassiveDataSource` writes from an `asyncio.to_thread` worker thread).

```python
class PriceCache:
    def update(self, ticker: str, price: float, timestamp: float | None = None) -> PriceUpdate
    def get(self, ticker: str) -> PriceUpdate | None
    def get_price(self, ticker: str) -> float | None
    def get_all(self) -> dict[str, PriceUpdate]      # shallow copy
    def remove(self, ticker: str) -> None
    @property
    def version(self) -> int                          # monotonic; +1 per update
    def __len__(self) -> int
    def __contains__(self, ticker: str) -> bool
```

`update()` computes `previous_price`, so callers pass a bare float and cannot construct an inconsistent `PriceUpdate`.

**The `version` counter** is how SSE avoids re-sending unchanged data: the generator remembers the last version it sent and skips the tick when nothing moved. Cheap, and it makes an idle simulator produce an idle stream.

> **Known limitation.** `version` is a global counter, not per-ticker, so any single ticker's update marks the whole snapshot dirty and the SSE endpoint re-sends **all** tickers. Fine at 10–50 tickers; if per-ticker deltas are ever needed, add `dict[str, int]` versions rather than diffing payloads client-side.

---

## 5. `create_market_data_source()` — source selection

`app/market/factory.py`. The **only** place the environment is consulted.

```python
def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if api_key:
        logger.info("Market data source: Massive API (real data)")
        return MassiveDataSource(api_key=api_key, price_cache=price_cache)
    logger.info("Market data source: GBM Simulator")
    return SimulatorDataSource(price_cache=price_cache)
```

Rules:

- **`.strip()` matters** — `.env` files routinely contain `MASSIVE_API_KEY=` or a stray space; whitespace must mean "absent", per PLAN.md §5.
- Returns an **unstarted** source; the caller owns `start()`. Keeps the factory synchronous and testable.
- The env var is read here, not by the SDK, which sidesteps the SDK's import-time default-argument trap (`MASSIVE_API.md` §3).
- Logs the selected source at INFO — the first thing to check when prices look wrong.

### Recommended addition: plan-aware polling and startup validation

Because Massive's free tier excludes snapshots entirely (`MASSIVE_API.md` §2), a key alone does not mean live prices are available. Make the interval configurable and verify the key before trusting it:

```python
poll_interval = float(os.environ.get("MASSIVE_POLL_INTERVAL", "15"))
return MassiveDataSource(api_key=api_key, price_cache=price_cache, poll_interval=poll_interval)
```

| Plan | Snapshot available | Sensible interval |
|---|---|---|
| Basic (free) | ❌ excluded | n/a — falls back to simulator |
| Starter / Developer | ✅ 15-min delayed | 15 s (data only changes each minute bar) |
| Advanced+ | ✅ real-time | 2–5 s |

**Fail fast, then degrade.** `MassiveDataSource.start()` already performs one immediate poll. Make that poll's outcome decide the source:

```python
source = MassiveDataSource(api_key=api_key, price_cache=cache)
try:
    await source.start(tickers)
    if len(cache) == 0:                       # authenticated but nothing usable
        raise RuntimeError("Massive returned no usable prices (plan entitlement?)")
except Exception as e:
    logger.error("Massive unavailable (%s) — falling back to simulator", e)
    await source.stop()
    source = SimulatorDataSource(price_cache=cache)
    await source.start(tickers)
```

This turns the single worst failure mode — a valid free-tier key producing a permanently empty, silently broken UI — into a logged fallback with a working app.

---

## 6. Data the interface deliberately does not carry

**Day-change percentage.** The watchlist shows "daily change %", but `PriceUpdate.change_percent` is tick-over-tick. The simulator has no concept of a session close, and Massive supplies it as `todays_change_percent` / `prev_day.close`. Options, in preference order:

1. **Frontend-derived (current, implicit).** First price received after page load is the baseline. Simple, source-agnostic, but resets on refresh and is not a true daily change.
2. **Add `previous_close` to `PriceUpdate`** — Massive fills it from `prev_day.close`; the simulator records its seed price at start-of-session. Correct for both sources, and a one-field change that keeps the SSE payload the single source of truth.

Recommend option 2 if daily change is meant to be accurate; it is the only field the current model is genuinely missing.

**Market hours.** With a real key, prices are static outside 09:30–16:00 ET — the watchlist looks frozen or broken. The simulator ticks forever. Surface it rather than hide it: call `client.get_market_status()` on the poll loop (or read `market_status` from `/v3/snapshot`) and expose it so the header can show "Market closed — last close shown". Do **not** synthesise fake movement onto real data.

**OHLC / historical bars.** Out of scope for this interface, which is a *latest price* contract. Detail charts accumulate from the SSE stream (PLAN.md §2). If real history is added later, it belongs in a separate read-through service, not on `MarketDataSource`.

---

## 7. Required corrections to `MassiveDataSource`

`backend/app/market/massive_client.py` cannot currently populate the cache from the real API. Verified against `TickerSnapshot` objects parsed by the installed SDK:

```python
# CURRENT — lines 99-108. Both lines are wrong.
price = snap.last_trade.price                 # AttributeError when plan omits trades (Starter)
timestamp = snap.last_trade.timestamp / 1000.0  # AttributeError ALWAYS: no such field
```

`LastTrade` has **no `timestamp`** attribute (it is `sip_timestamp`, in **nanoseconds**). Every snapshot therefore raises `AttributeError`, which the surrounding `except (AttributeError, TypeError)` catches, logs at WARNING, and skips — the cache stays empty while the app looks healthy. Replace with the ladder from `MASSIVE_API.md` §5:

```python
for snap in snapshots:
    price = extract_price(snap)              # last_trade → min → day → prev_day
    if price is None:
        logger.warning("No usable price for %s", getattr(snap, "ticker", "???"))
        continue
    self._cache.update(
        ticker=snap.ticker,
        price=price,
        timestamp=extract_timestamp(snap) or time.time(),   # ns → seconds
    )
    processed += 1
```

Also worth fixing while in here:

- **`_poll_loop` sleeps first.** `start()` polls once, then the loop sleeps before polling again — correct, but a poll that raises inside `start()` currently propagates and aborts startup. Decide deliberately (see §5's fail-fast block) rather than by accident.
- **Broad `except Exception` in `_poll_once` hides permanent failures.** A 401/403 will retry silently every 15 s forever. Distinguish `BadResponse` containing 401/403 (permanent → stop polling, log loudly, consider fallback) from transient errors (retry).
- **Tickers are upper-cased in `add_ticker`/`remove_ticker` but not in `start()`.** Massive tickers are case-sensitive; normalise in one place so a lower-case watchlist row from the DB cannot silently produce no data.
- **Set tighter timeouts** — `RESTClient(api_key=..., connect_timeout=5, read_timeout=5)` so a hung request cannot outlive its poll interval.

### The testing change that matters

`tests/market/test_massive.py` builds snapshots with `MagicMock`, which fabricates **any** attribute on access — so `snap.last_trade.timestamp` "works" in tests and fails in production. 13 passing tests did not catch a total failure of the integration.

**Build fixtures from real SDK models instead**, parsing a documented payload through the SDK's own deserialiser:

```python
from massive.rest.models.snapshot import TickerSnapshot

def make_snapshot(ticker="AAPL", price=190.50, ts_ns=1675190399000000000, with_trade=True):
    raw = {
        "ticker": ticker,
        "todaysChange": -4.54, "todaysChangePerc": -3.50, "updated": ts_ns,
        "day":     {"o": 129.61, "h": 130.15, "l": 125.07, "c": price, "v": 111237700},
        "prevDay": {"o": 128.0, "h": 130.0, "l": 127.0, "c": 129.61, "v": 98000000},
        "min":     {"av": 111237700, "o": 125.1, "h": 125.2, "l": 125.0, "c": price, "t": 1675190340000},
    }
    if with_trade:                     # omit to simulate a Starter-plan response
        raw["lastTrade"] = {"p": price, "s": 100, "x": 4, "t": ts_ns, "c": [1]}
    return TickerSnapshot.from_dict(raw)
```

Cases worth covering: trades present; trades absent (Starter); `min`/`day` absent (pre-open); nanosecond timestamp lands in a sane range after conversion; `BadResponse` on poll leaves the previous cache contents intact.

---

## 8. Consuming the interface

```python
from app.market import PriceCache, create_market_data_source, create_stream_router

# --- startup ---
cache = PriceCache()
source = create_market_data_source(cache)
await source.start(["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA",
                    "NVDA", "META", "JPM", "V", "NFLX"])
app.include_router(create_stream_router(cache))

# --- price a trade (never awaits I/O) ---
price = cache.get_price(ticker)
if price is None:
    raise HTTPException(400, f"No price available for {ticker}")
fill_value = price * quantity

# --- value the portfolio ---
prices = cache.get_all()
total = cash + sum(pos.quantity * prices[pos.ticker].price
                   for pos in positions if pos.ticker in prices)

# --- watchlist mutation, keep DB and source in step ---
await source.add_ticker(ticker)

# --- shutdown ---
await source.stop()
```

Rules for consumers:

1. **Always handle `None` from the cache.** A just-added ticker has no price for up to one poll interval (15 s on Massive). Reject the trade with a clear error; never default to 0.
2. **Never call the source for a price.** `get_tickers()` is the only read on the source.
3. **Positions can outlive the watchlist.** Removing a ticker drops it from the cache while a position may still exist — portfolio valuation must skip missing tickers (as above) or the total silently goes wrong. Consider keeping `union(watchlist, position_tickers)` tracked instead of the watchlist alone.
4. **Ownership: whoever mutates the DB watchlist also mutates the source**, in that order, in the same request handler.

---

## 9. SSE integration

`app/market/stream.py` — `create_stream_router(cache)` returns a router serving `GET /api/stream/prices`.

- Emits `retry: 1000` first, so `EventSource` reconnects after 1 s.
- Sends the **full snapshot** of all tickers as one JSON object keyed by ticker, every 500 ms, but only when `cache.version` changed.
- Stops on `request.is_disconnected()`; swallows `CancelledError` on shutdown.
- Headers: `Cache-Control: no-cache`, `X-Accel-Buffering: no` (defeats proxy buffering).

```
data: {"AAPL": {"ticker":"AAPL","price":190.5,"previous_price":190.42,
                "timestamp":1.75e9,"change":0.08,"change_percent":0.042,
                "direction":"up"}, ...}
```

Note the router is created by a factory over a **module-level `APIRouter`**, so calling `create_stream_router()` twice registers `/prices` twice. Harmless in the single-call app; if it's ever called per-test, build the `APIRouter` inside the factory instead.

---

## 10. File layout

```
backend/app/market/
├── __init__.py          # public API: PriceUpdate, PriceCache, MarketDataSource,
│                        #             create_market_data_source, create_stream_router
├── models.py            # PriceUpdate
├── interface.py         # MarketDataSource ABC
├── cache.py             # PriceCache
├── factory.py           # create_market_data_source()  ← only env-var read
├── simulator.py         # GBMSimulator + SimulatorDataSource
├── seed_prices.py       # seed prices, per-ticker GBM params, correlation groups
├── massive_client.py    # MassiveDataSource (REST poller)
└── stream.py            # create_stream_router() — SSE endpoint
```

Import only from `app.market`, never from submodules — the `__init__.py` surface is the supported contract.

---

## 11. Adding a third source

The interface is designed so a new provider touches two files. To add e.g. an Alpaca or IEX source:

1. Implement `MarketDataSource` in `app/market/<provider>_client.py`; normalise timestamps to epoch seconds and write only via `cache.update()`.
2. Add the branch to `create_market_data_source()`.
3. Reuse the shared contract tests (§7) so the new source is held to the same behaviour.

Nothing else in the codebase changes — which is the point of the design.
