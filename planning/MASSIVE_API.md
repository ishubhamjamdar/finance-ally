# Massive API Reference (formerly Polygon.io)

Reference for retrieving real-time and end-of-day prices for multiple tickers from Massive, as used by FinAlly's `MassiveDataSource`.

> **Verification basis.** Every field name, method signature, and default in this document was verified by introspecting the installed `massive` Python SDK (checked on both **2.8.0** — latest — and **2.2.0** — the version pinned in `backend/uv.lock`; the models below are identical on both). Plan entitlements, rate limits and timestamp units were verified against the live docs at massive.com. Where this document contradicts `planning/archive/MASSIVE_API.md`, this document is correct — see [Corrections](#corrections-to-earlier-docs-and-current-code).

---

## 1. Overview

| Item | Value |
|---|---|
| Rebrand | Polygon.io became **Massive** on 30 October 2025 |
| Base URL | `https://api.massive.com` (SDK default; legacy `https://api.polygon.io` still resolves) |
| Python package | `massive` (`uv add massive`) — repo moved to `github.com/massive-com/client-python` |
| Import | `from massive import RESTClient, WebSocketClient` |
| Auth header | `Authorization: Bearer <API_KEY>` (SDK adds this automatically) |
| Env var read by SDK | `MASSIVE_API_KEY` |
| Coverage | All US exchanges incl. dark pools and OTC; equities, options, indices, forex, crypto, futures |

---

## 2. Plans, rate limits, and data recency

This table drives FinAlly's polling design. **The free tier does not provide live prices.**

| Plan | Price | Rate limit | Recency | Trades/quotes included |
|---|---|---|---|---|
| **Stocks Basic** | $0 | **5 req/min** | **End-of-day only** | No |
| **Stocks Starter** | $29/mo | Unlimited | 15-min delayed | No (aggregates only) |
| **Stocks Developer** | $79/mo | Unlimited | 15-min delayed | **Trades** included |
| **Stocks Advanced** | $199/mo | Unlimited | **Real-time** | Trades + quotes |
| Stocks Business | Contact | Unlimited | Real-time | Trades + quotes |

Individual plans are restricted to non-professional use.

### Endpoint availability by plan

| Endpoint | Basic (free) | Starter | Developer | Advanced |
|---|---|---|---|---|
| `/v2/snapshot/.../tickers` (Full Market Snapshot) | ❌ **excluded** | ✅ 15-min | ✅ 15-min | ✅ real-time |
| `/v3/snapshot` (Unified Snapshot) | ❌ **excluded** | ✅ 15-min | ✅ 15-min | ✅ real-time |
| `/v2/aggs/grouped/...{date}` (Daily Market Summary) | ✅ **EOD** | ✅ | ✅ | ✅ |
| `/v2/aggs/ticker/{t}/prev` (Previous Close) | ✅ | ✅ | ✅ | ✅ |
| `/v2/aggs/ticker/{t}/range/...` (Aggregates/bars) | ✅ (2y history) | ✅ (5y) | ✅ (10y) | ✅ (20y+) |
| `/v2/last/trade/{t}` (Last Trade) | ❌ | ❌ | ✅ | ✅ |
| WebSocket streams | ❌ | ✅ (delayed feed) | ✅ | ✅ (real-time feed) |

**Consequences for FinAlly:**

1. A `MASSIVE_API_KEY` on the free Basic plan **cannot** serve the live watchlist — snapshot calls return non-200 and the SDK raises `BadResponse`. Use grouped daily aggs (EOD) or fall back to the simulator.
2. On **Starter**, snapshots come back **without a `lastTrade` object** (no trades entitlement). Code that reads `snapshot.last_trade.price` gets `AttributeError: 'NoneType'`. A fallback ladder is mandatory — see §5.
3. Only **Advanced+** gives genuinely real-time prices. Starter/Developer are 15 minutes delayed, so a "live" FinAlly watchlist on those plans lags the market by 15 minutes.

---

## 3. Client initialisation

```python
from massive import RESTClient

# Recommended for FinAlly: pass the key explicitly.
client = RESTClient(api_key=api_key)
```

Full signature (verified):

```python
RESTClient(
    api_key: str | None = os.getenv("MASSIVE_API_KEY"),  # evaluated at IMPORT time
    connect_timeout: float = 10.0,
    read_timeout: float = 10.0,
    num_pools: int = 10,
    retries: int = 3,
    base: str = "https://api.massive.com",
    pagination: bool = True,
    verbose: bool = False,   # sets SDK logger to DEBUG
    trace: bool = False,     # logs request URLs/headers, API key redacted
    custom_json: Any | None = None,
)
```

> ⚠️ **Import-time default gotcha.** `api_key`'s default is `os.getenv(ENV_KEY)` evaluated when `massive.rest` is *imported*, not when `RESTClient()` is called. If you load `.env` (e.g. `python-dotenv`) *after* importing `massive`, `RESTClient()` sees `None` and raises `AuthError("Must specify env var MASSIVE_API_KEY or pass api_key in constructor")`. **Always pass `api_key=` explicitly** — which is what FinAlly's factory does by reading the env var itself.

The client is **synchronous** (urllib3 `PoolManager`). In FastAPI, always wrap calls in `asyncio.to_thread(...)` so the event loop is not blocked.

---

## 4. Primary endpoint — Full Market Snapshot (multiple tickers, one call)

The workhorse for FinAlly: current data for every watchlist ticker in **a single request**, which is what keeps request counts flat as the watchlist grows.

**REST:** `GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,GOOGL,MSFT`

```python
from massive import RESTClient
from massive.rest.models import SnapshotMarketType

client = RESTClient(api_key=api_key)

snapshots = client.get_snapshot_all(
    market_type=SnapshotMarketType.STOCKS,      # "stocks" | "forex" | "crypto" | "indices"
    tickers=["AAPL", "GOOGL", "MSFT"],          # list is joined to a CSV internally
    include_otc=False,
)

for snap in snapshots:                          # list[TickerSnapshot]
    print(snap.ticker, snap.last_trade.price, snap.todays_change_percent)
```

Signature:

```python
get_snapshot_all(
    market_type: str | SnapshotMarketType,
    tickers: str | list[str] | None = None,     # None = all US tickers (large response)
    params: dict | None = None,
    raw: bool = False,
    include_otc: bool | None = False,
    options: RequestOptionBuilder | None = None,
) -> list[TickerSnapshot] | HTTPResponse
```

### `TickerSnapshot` — exact field names

The SDK renames the wire keys. **Use the Python names on the left, not the JSON keys.**

| Python attribute | Wire key | Type | Notes |
|---|---|---|---|
| `ticker` | `ticker` | `str` | |
| `todays_change` | `todaysChange` | `float` | vs previous close |
| `todays_change_percent` | `todaysChangePerc` | `float` | **not** `day.change_percent` |
| `updated` | `updated` | `int` | **nanoseconds** since epoch |
| `day` | `day` | `Agg` | today's bar so far |
| `prev_day` | `prevDay` | `Agg` | previous session's bar |
| `min` | `min` | `MinuteSnapshot` | most recent minute bar |
| `last_trade` | `lastTrade` | `LastTrade \| None` | **`None` unless plan includes trades** |
| `last_quote` | `lastQuote` | `LastQuote \| None` | **`None` unless plan includes quotes** |
| `fair_market_value` | `fmv` | `float \| None` | Business plans only |

`Agg` (used for `day` and `prev_day`): `open` `high` `low` `close` `volume` `vwap` `timestamp` `transactions` `otc`
→ wire keys `o` `h` `l` `c` `v` `vw` `t` `n` `otc`. **There is no `previous_close` field on `day`** — use `prev_day.close`.

`MinuteSnapshot`: `accumulated_volume` (`av`), `open`, `high`, `low`, `close`, `volume`, `vwap`, `timestamp` (`t`), `transactions`.

`LastTrade`: `price` (`p`), `size` (`s`), `exchange` (`x`), `conditions` (`c`), `id` (`i`), `sip_timestamp` (`t`), `participant_timestamp` (`y`), `trf_timestamp` (`f`), `sequence_number` (`q`), `correction` (`e`), `tape` (`z`).
> ⚠️ **There is no `LastTrade.timestamp`.** The timestamp is `sip_timestamp`, in **nanoseconds**.

`LastQuote`: `bid_price` (`p`), `bid_size` (`s`), `ask_price` (`P`), `ask_size` (`S`), `sip_timestamp` (`t`), plus exchange/tape fields. No `spread` field — compute `ask_price - bid_price` yourself.

### Raw JSON, for reference

```json
{
  "status": "OK", "count": 1,
  "tickers": [{
    "ticker": "AAPL",
    "todaysChange": -4.54,
    "todaysChangePerc": -3.50,
    "updated": 1675190399000000000,
    "day":     {"o":129.61,"h":130.15,"l":125.07,"c":125.07,"v":111237700,"vw":127.35},
    "prevDay": {"o":128.00,"h":130.00,"l":127.00,"c":129.61,"v":98000000,"vw":128.90},
    "min":     {"av":111237700,"o":125.10,"h":125.20,"l":125.00,"c":125.07,"v":50000,"t":1675190340000},
    "lastTrade": {"p":125.07,"s":100,"x":4,"t":1675190399000000000,"c":[1],"i":"12345"},
    "lastQuote": {"P":125.08,"S":1000,"p":125.06,"s":500,"t":1675190399500000000}
  }]
}
```

Snapshot data is cleared daily (~3:30 am ET) and repopulates from ~4:00 am ET as exchange data arrives.

---

## 5. Extracting a usable price (the fallback ladder)

Because `last_trade` and `last_quote` depend on entitlements and any field can be `None`, never read one path unconditionally. Prefer freshest-available:

```python
def extract_price(snap) -> float | None:
    """Freshest available price, degrading gracefully across plan tiers."""
    if snap.last_trade is not None and snap.last_trade.price:
        return snap.last_trade.price          # Developer+ : actual last trade
    if snap.min is not None and snap.min.close:
        return snap.min.close                 # Starter    : latest minute bar close
    if snap.day is not None and snap.day.close:
        return snap.day.close                 # today's bar so far
    if snap.prev_day is not None and snap.prev_day.close:
        return snap.prev_day.close            # pre-open / stale fallback
    return None


def extract_timestamp(snap) -> float | None:
    """Epoch SECONDS. Snapshot timestamps are NANOSECONDS."""
    if snap.last_trade is not None and snap.last_trade.sip_timestamp:
        return snap.last_trade.sip_timestamp / 1e9
    if snap.updated:
        return snap.updated / 1e9
    return None
```

### Timestamp units — do not guess

| Field | Unit | Convert to seconds |
|---|---|---|
| `TickerSnapshot.updated` | nanoseconds | `/ 1e9` |
| `LastTrade.sip_timestamp` | nanoseconds | `/ 1e9` |
| `LastQuote.sip_timestamp` | nanoseconds | `/ 1e9` |
| `MinuteSnapshot.timestamp` | milliseconds (documented as ns in places — normalise defensively) | see helper |
| `Agg.timestamp` (aggregates, grouped daily) | milliseconds | `/ 1e3` |
| `PreviousCloseAgg.timestamp` | milliseconds | `/ 1e3` |

Given the inconsistency between endpoints, normalise by magnitude rather than trusting the unit:

```python
def to_epoch_seconds(raw: int | float | None) -> float | None:
    """Normalise s / ms / us / ns to epoch seconds by order of magnitude."""
    if not raw:
        return None
    v = float(raw)
    for divisor in (1.0, 1e3, 1e6, 1e9):
        candidate = v / divisor
        if 1e9 < candidate < 4e9:   # ~2001..2096
            return candidate
    return None
```

> Getting this wrong is not a subtle failure: dividing a nanosecond timestamp by 1000 yields epoch 1.675e15 — **year ~53,000,000** — which overflows `datetime.fromtimestamp()` with `ValueError: year must be in 1..9999`.

---

## 6. Unified Snapshot (`/v3/snapshot`) — the modern alternative

Newer, cross-asset, and returns a richer `session` object that includes `previous_close` and `change_percent` directly, so no second call is needed for day-change figures.

```python
snapshots = list(client.list_universal_snapshots(
    type="stocks",
    ticker_any_of=["AAPL", "GOOGL", "MSFT"],   # max 250 tickers
    limit=250,                                  # ⚠️ SDK default is 10
))

for s in snapshots:
    print(s.ticker, s.session.price, s.session.change_percent, s.market_status)
```

- `ticker_any_of` accepts **up to 250** tickers per call (watch URL length limits).
- ⚠️ **`limit` defaults to `10`.** With `pagination=True` the returned iterator transparently pages, so you still get all results — but as many extra HTTP requests as pages. Always pass `limit=250`.
- `UniversalSnapshot.session` fields: `price`, `change`, `change_percent`, `open`, `high`, `low`, `close`, `previous_close`, `volume`, `vwap`, `early_trading_change(_percent)`, `regular_trading_change(_percent)`, `late_trading_change(_percent)`, `last_updated`.
- `market_status` — `"open" | "closed" | "early_trading" | "late_trading"`, useful for deciding whether a frozen price is expected.
- Per-ticker `error` / `message` fields report failures for individual tickers without failing the whole request.
- Excluded from Basic; 15-min delayed on Starter/Developer; real-time on Advanced+.

**Recommendation:** `/v2/snapshot` (`get_snapshot_all`) remains the better default for FinAlly — flat list, no pagination concerns, no 250-ticker cap. Prefer `/v3` if you want `session.previous_close` and `market_status` without extra plumbing.

---

## 7. End-of-day prices for multiple tickers

### Daily Market Summary — all US tickers, one call, works on the free tier

The correct EOD endpoint for multi-ticker use, and the **only** bulk price endpoint available on Basic.

**REST:** `GET /v2/aggs/grouped/locale/us/market/stocks/{date}`

```python
bars = client.get_grouped_daily_aggs(
    date="2026-07-24",     # str or datetime.date; must be a trading day
    adjusted=True,         # split-adjusted (default true)
    include_otc=False,
)

by_ticker = {b.ticker: b for b in bars}          # ~10,000 results
aapl = by_ticker["AAPL"]
print(aapl.close, aapl.open, aapl.high, aapl.low, aapl.volume, aapl.vwap)
```

`GroupedDailyAgg` fields: `ticker` (`T`), `open`, `high`, `low`, `close`, `volume`, `vwap`, `timestamp` (`t`, **milliseconds**), `transactions` (`n`), `otc`.

Notes:
- Returns **every** US ticker for that date — one call, then filter locally. Ideal under a 5 req/min cap.
- A non-trading date (weekend/holiday) returns an empty `results` array. Use `get_market_status()` or walk back a day.
- On Basic this is EOD only, so it's a "yesterday's close" source, not a live feed.

### Previous close — single ticker

```python
prev = client.get_previous_close_agg(ticker="AAPL")   # -> PreviousCloseAgg
print(prev.close, prev.open, prev.high, prev.low, prev.volume, prev.vwap)
```
Fields: `ticker`, `open`, `high`, `low`, `close`, `volume`, `vwap`, `timestamp` (ms). Note this returns a **single object**, not a list — earlier FinAlly docs iterated over it, which is wrong.

### Daily open/close — one ticker, one date, with pre/post

```python
oc = client.get_daily_open_close_agg(ticker="AAPL", date="2026-07-24")
print(oc.open, oc.close, oc.pre_market, oc.after_hours, oc.status)
```
Fields: `symbol`, `from_`, `open`, `high`, `low`, `close`, `pre_market`, `after_hours`, `volume`, `status`, `otc`.

### Historical bars (for charts)

```python
for agg in client.list_aggs(
    ticker="AAPL", multiplier=1, timespan="day",      # second|minute|hour|day|week|month|quarter|year
    from_="2026-01-01", to="2026-07-24",
    adjusted=True, sort="asc", limit=50000,
):
    print(agg.timestamp, agg.open, agg.high, agg.low, agg.close, agg.volume)
```
`list_aggs` returns a paginating **iterator**; `get_aggs` returns a plain list. Both accept `str | int | datetime | date` for `from_`/`to`.

---

## 8. Other useful endpoints

```python
# Market open/closed — decide whether a static price is expected or a fault
status = client.get_market_status()
print(status.market, status.early_hours, status.after_hours, status.server_time)

# Trading-day calendar
holidays = client.get_market_holidays()

# Single-ticker snapshot (detail view)
snap = client.get_snapshot_ticker(market_type=SnapshotMarketType.STOCKS, ticker="AAPL")

# Last trade / last NBBO quote (Developer+ / Advanced+)
trade = client.get_last_trade(ticker="AAPL")     # .price, .size, .sip_timestamp (ns)
quote = client.get_last_quote(ticker="AAPL")     # .bid_price, .ask_price, .bid_size, .ask_size

# Ticker metadata for watchlist validation (company name, active flag)
details = client.get_ticker_details(ticker="AAPL")

# Server-side technical indicators, if ever wanted
sma = client.get_sma(ticker="AAPL", timespan="day", window=50)
```

`client.get_summaries(ticker_any_of=[...])` also returns multi-ticker summary data. The SDK exposes 88 REST methods in total (options, futures, financials, news, dividends, splits, short interest, Benzinga, treasury yields, …) — see `dir(RESTClient)`.

---

## 9. Real-time via WebSocket (alternative to polling)

FinAlly deliberately polls REST (per PLAN.md §6: simpler, works on all tiers). For reference, the streaming path:

```python
from massive import WebSocketClient
from massive.websocket.models import Market, Feed

ws = WebSocketClient(
    api_key=api_key,
    feed=Feed.RealTime,            # Feed.Delayed for 15-min delayed entitlements
    market=Market.Stocks,
    subscriptions=["T.AAPL", "T.GOOGL"],   # T=trades, Q=quotes, A=second aggs, AM=minute aggs
    max_reconnects=5,
)

def handle(msgs):
    for m in msgs:                 # EquityTrade: .symbol .price .size .timestamp (ms)
        price_cache.update(m.symbol, m.price)

ws.run(handle)                     # blocking; use ws.connect(...) inside asyncio
```

Feeds: `socket.massive.com` (real-time), `delayed.massive.com` (15-min), plus `starterfeed`/`polyfeed`/`business` variants per plan. ~25 ms latency. Requires Starter+.

**Why FinAlly doesn't use it:** it adds reconnection/backpressure handling and per-plan feed-host selection for no benefit at a 500 ms UI refresh, and it's unavailable on Basic.

---

## 10. Error handling

The SDK exposes exactly two exception types (`massive.exceptions`):

| Exception | Raised when |
|---|---|
| `AuthError` | `api_key` is `None`/empty at construction |
| `BadResponse` | **Any** non-200 response; the message is the raw response body |

`BadResponse` is a single flat type — there are no status-specific subclasses, so to branch on the cause you must inspect the message string. Meanings:

| Status | Cause | Handling |
|---|---|---|
| 401 | Invalid/revoked key | Fatal — log clearly, fall back to simulator |
| 403 | **Plan lacks entitlement** (e.g. snapshot on Basic) | Fatal for that endpoint — degrade to grouped-daily or simulator |
| 429 | Rate limit (Basic: 5 req/min) | Retried automatically; then back off |
| 5xx | Server error | Retried automatically |

Built-in retry (urllib3 `Retry`, verified): `total=retries` (default **3**), `backoff_factor=0.1` → 0.0 s, 0.2 s, 0.4 s, 0.8 s…, on `status_forcelist=[413, 429, 499, 500, 502, 503, 504]`. Note **401/403 are not retried** (correctly — they are permanent). Once retries are exhausted, urllib3 raises `MaxRetryError`, which is *not* a `BadResponse` — so catch broadly:

```python
from massive.exceptions import AuthError, BadResponse

try:
    snapshots = await asyncio.to_thread(fetch)
except BadResponse as e:
    logger.error("Massive returned an error: %s", e)      # 401/403/4xx
except Exception as e:
    logger.error("Massive request failed: %s", e)         # MaxRetryError, timeouts, DNS
```

Timeouts default to 10 s connect / 10 s read. For a 15 s poll interval, lower them (`connect_timeout=5, read_timeout=5`) so a hung request cannot overrun the next tick.

---

## 11. Corrections to earlier docs and current code

Verified by executing the current extraction logic against real `TickerSnapshot` objects parsed by the SDK:

| # | Claim in `archive/MASSIVE_API.md` / code in `massive_client.py` | Reality |
|---|---|---|
| 1 | `snap.last_trade.timestamp` | **No such attribute.** Raises `AttributeError: 'LastTrade' object has no attribute 'timestamp'`. Correct field: `sip_timestamp`. |
| 2 | Timestamps are Unix **milliseconds**; `/ 1000.0` | Snapshot/trade timestamps are **nanoseconds**; divide by `1e9`. `/1000` yields year ~53,000,000. |
| 3 | `day.previous_close`, `day.change_percent` | Neither exists on `Agg`. Use `prev_day.close` and `todays_change_percent`. |
| 4 | `last_quote.spread` | Not a field — compute `ask_price - bid_price`. |
| 5 | `get_previous_close_agg` returns an iterable to loop over | Returns a **single** `PreviousCloseAgg`. |
| 6 | "Free tier (5 calls/min): poll every 15 seconds" (also PLAN.md §6) | Basic is **EOD-only and excludes snapshots entirely**. Polling every 15 s returns errors, not delayed prices. |
| 7 | `RESTClient()` "reads MASSIVE_API_KEY from environment automatically" | True only if the env var is set **before** `massive` is imported (import-time default arg). Pass `api_key=` explicitly. |
| 8 | Paid tiers "unlimited, stay under 100 req/s" | Starter+ are unlimited; no documented 100 req/s guidance found. |

**Live impact on `backend/app/market/massive_client.py`:** lines 99–108 read `snap.last_trade.price` and `snap.last_trade.timestamp / 1000.0` inside `except (AttributeError, TypeError)`. Against the real SDK, **every** snapshot raises `AttributeError` on `.timestamp`, is caught, logged at WARNING, and skipped — so with a valid key the price cache silently never populates. The existing tests miss this because `_make_snapshot()` builds a `MagicMock`, which auto-creates any attribute accessed, including `.timestamp`. See `MARKET_INTERFACE.md` §7 for the fix and the testing change that would have caught it.

---

## Sources

- [Polygon.io is Now Massive](https://massive.com/blog/polygon-is-now-massive)
- [Massive API Docs](https://massive.com/docs)
- [Stocks REST API Overview](https://massive.com/docs/rest/stocks/overview)
- [Full Market Snapshot](https://massive.com/docs/rest/stocks/snapshots/full-market-snapshot)
- [Unified Snapshot](https://massive.com/docs/rest/stocks/snapshots/unified-snapshot)
- [Daily Market Summary (grouped daily)](https://massive.com/docs/rest/stocks/aggregates/daily-market-summary)
- [Pricing and plan limits](https://massive.com/pricing)
- [Massive + Python](https://massive.com/blog/polygon-io-with-python-for-stock-market-data)
- SDK introspection: `massive` 2.8.0 and 2.2.0 (`RESTClient`, `massive.rest.models.*`, `massive.rest.base`, `massive.exceptions`)
