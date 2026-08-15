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

Reach the live objects through `request.app.state`, never a module global — and
re-read them per request, because failover replaces `market_source` mid-session.
`market_source` is also `None` before startup, after shutdown, and after a
failover that could not start a replacement.

Handlers reach both through `app/api/deps.py` rather than `app.state`:

```python
from app.api.deps import get_market_source, get_price_cache

price_cache: Annotated[PriceCache, Depends(get_price_cache)]
source: Annotated[MarketDataSource, Depends(get_market_source)]
```

`get_market_source` raises `HTTPException(503)` when no source is running, once,
so no handler repeats the check — and injecting them makes
`app.dependency_overrides` available, which is how the API tests point handlers
at a fixed cache instead of starting a simulator. `/api/health` is the
deliberate exception: reporting "no source" is its job, so it reads `app.state`
directly.

**Depend on `get_market_source` even where the source is unused.** `POST
/api/portfolio/trade` fills from the cache and never touches the source, but
takes the dependency anyway: without it, a failover that could not start a
replacement leaves every price frozen and the endpoint filling against them
indefinitely, while the watchlist endpoints next door return 503.

The lifespan also runs a background task writing a `portfolio_snapshots` row
every `SNAPSHOT_INTERVAL_SECONDS` (30). It writes its first point immediately,
so a fresh app has a P&L chart with a line on it, and it is cancelled *and
awaited* at shutdown.

### Handler colour: `async def` only when the source is awaited

A `def` handler runs in a worker thread automatically, which is what the
blocking SQLite calls want. Use it for anything that only touches the database
and the cache — the three portfolio endpoints and `GET /api/watchlist`.

The two watchlist mutations are the exception: they must write the row *and*
`await source.add_ticker()`, which a `def` handler cannot do. Those are
`async def` with the database work pushed to a thread:

```python
from fastapi.concurrency import run_in_threadpool

@router.post("")
async def create_watchlist_entry(payload: WatchlistAddRequest, source=Depends(get_market_source)):
    ticker = normalize_ticker(payload.ticker)
    entry = await run_in_threadpool(_insert_watchlist_row, ticker)
    await source.add_ticker(ticker)
```

Never split those two across handlers: a watchlist row whose ticker the source
never heard of is a row that never gets a price (MARKET_DATA_DESIGN.md §13.4).
Database write first, source second, one handler — and undo the row if the
source refuses, which `app/api/watchlist.py` does in both directions.

Static files are mounted at `/` **after** every router. `StaticFiles` at the
root matches every path, so mounting it earlier would swallow `/api/*`.

## Database API

```python
from app.db import connect, transaction, read_transaction, load_tracked_tickers, utc_now
```

Import from `app.db` only, the same contract `app.market` keeps.

- **`connect()`** — context manager yielding an initialised `sqlite3.Connection`
  with `row_factory = sqlite3.Row`. Autocommit: each statement commits alone.
  Correct for a *single* query and wrong for several
- **`transaction()`** — `connect()` wrapped in `BEGIN IMMEDIATE`. Use it for
  anything that must land atomically — a trade touches `positions`, `trades`,
  `users_profile` and `portfolio_snapshots` and must not land partially. Taking
  the write lock up front is also what stops two concurrent buys both reading
  the same cash balance
- **`read_transaction()`** — `BEGIN DEFERRED`: one consistent snapshot across
  several reads, without taking a write lock. Valuing the portfolio reads cash
  and then positions, and in autocommit a trade committing between the two
  yields pre-trade cash beside a post-trade position. **Any multi-statement
  read goes through this**
- **`load_tracked_tickers()`** — `union(watchlist, positions)`. Positions are in
  the union deliberately: a ticker removed from the watchlist while still held
  must keep being priced or the portfolio total silently loses it
- **`utc_now()`** — the ISO-8601 string every `*_at` column stores

### Repository and domain layers

`app/db/repository.py` holds row-level access for the six tables. Every function
takes an open connection as its first argument, which is what lets a trade
compose four of them inside one `transaction()`. Nothing there validates.

`app/portfolio.py` and `app/watchlist.py` are the **only** implementations of
what a trade does, what the account is worth, and what the watchlist means.
They take no `Request` and raise no `HTTPException`, so Checkpoint 4's chat
handler calls them exactly as the routers do:

```python
from app.portfolio import TradeError, execute_trade
from app.watchlist import WatchlistError, add, remove, reconcile
```

`execute_trade` raises `TradeError`; the watchlist functions raise
`WatchlistError` subclasses. Catch those and report the message — never
re-implement the rule. A trade the LLM asks for must be validated exactly like
one the user typed.

**A trade is also refused when the price has stopped updating**, via
`price_cache.is_stale(ticker)` inside `_require_price`. `require_live_market`
catches a feed that is *gone*; this catches a poller wedged while its object is
still there, which is invisible to every other check and leaves every price
frozen at its last value. It lives in the domain layer rather than in a route
precisely so the chat path cannot bypass it. Valuation is deliberately **not**
subject to it: `get_portfolio` still answers with the last known marks.

`app/chat.py` is the third module of that kind and the one that proves the
point: it runs a whole conversational turn — context, prompt, model, parse,
execute, persist — and **executes nothing itself**. Every trade goes through
`execute_trade` and every watchlist change through `add`/`remove`. A model that
has been talked into anything is still a client, and a client cannot be trusted
to have validated its own request.

```python
from app.chat import handle_message, get_transcript   # no Request, no HTTPException
```

**The watchlist is capped at `MAX_WATCHLIST_SIZE` (50).** The check runs inside
the insert's own `BEGIN IMMEDIATE`, after `INSERT OR IGNORE` has decided the
duplicate question — which is what keeps "AAPL is already watched" reporting as
a duplicate rather than as a full list. `remove()`'s compensating restore passes
`enforce_cap=False`: putting back a row that was already there cannot be what
pushed the list over.

`app/portfolio.py`'s rounding policy — cash to cents via `_fill_value`,
quantities and `avg_cost` never rounded, display rounding through `_money` and
`_rate` — is documented at the top of that module. Do not re-decide it per call
site.

**The tracked set is `watchlist ∪ positions(quantity != 0)`, and
`watchlist.reconcile(source)` is its only enforcer.** Call it after any change
that could alter either side rather than adding or removing tickers by hand; it
is idempotent, and it re-reads after its removals so a buy landing mid-flight
cannot strand a holding with no price source.

One connection per operation, opened and closed inside the helper — about
500 µs each, most of it WAL sidecar setup, since no connection is held open.
Fine at human cadence; never put one on the 500 ms tick. Offload it from async
handlers with `run_in_threadpool` so it does not stall the event loop that
drives the simulator and every open SSE stream.

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

## LLM API

`app/llm/` is the contract with the model. Import from `app.llm` only, the same
rule `app.market` and `app.db` keep.

```python
from app.llm import LLMUnavailableError, MalformedReplyError, build_messages, complete, parse_reply
```

Four modules, and the split matters:

- **`schema.py`** — `AssistantReply` is the schema sent to the provider; nothing
  parses with it. `parse_reply` validates **action by action**, so one malformed
  trade costs that trade rather than the model's message and its nine good
  actions. Leniency here is salvage, never trust: survivors are re-validated by
  `app.portfolio` and `app.watchlist`
- **`prompt.py`** — rules and account data go in as `system` messages;
  everything a user or the model wrote goes in as `user`/`assistant`. That
  separation is this layer's whole contribution to prompt-injection defence. An
  assistant turn replays with *what actually executed* appended, because the
  model writes its message before knowing which trades cleared
- **`client.py`** — one blocking call. Callers on the event loop must push it to
  a thread. `LLM_MOCK` is honoured *here*, so mock and live runs share one parse
  and one execution path
- **`mock.py`** — returns raw JSON, not objects, for the same reason

**Two error types, deliberately not one.** `LLMUnavailableError` means the
provider could not be reached — a 503, nothing persisted, resend the message.
`MalformedReplyError` means it answered badly — a 200 whose message says so, and
the turn *is* recorded. Collapsing them would make a missing API key look like a
confused model.

**Do not add a `pattern`, `minLength` or similar keyword to the wire schema.**
Cerebras rejects them, and OpenRouter's `provider.order` is a preference rather
than a pin, so the request is silently served by another host — every call
succeeds, at none of the latency Cerebras was chosen for. `wire_schema()` strips
the known offenders and a test guards the list; `_log_provider` warns when
something else answered.

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
  - `age_of(ticker) -> float | None` — seconds since **this cache** was written,
    on the monotonic clock. Not `PriceUpdate.timestamp`, which is the venue's
    trade time and is hours old whenever the market is closed
  - `is_stale(ticker) -> bool` — has the entry outlived `staleness_limit`? False
    when no source has stamped one, and False for a ticker never written
  - `staleness_limit` — set by whichever source is writing; see below

- **`MarketDataSource`** — Abstract interface implemented by `SimulatorDataSource` and
  `MassiveDataSource`. Lifecycle: `start(tickers)` -> `add_ticker()` / `remove_ticker()` -> `stop()`.
  Two public attributes every source carries, so consumers never need `getattr` or `isinstance`:
  - `market_status` — `"open"`/`"closed"`/`"extended-hours"`, or `None` where the concept does not
    apply (the simulator always trades)
  - `on_permanent_failure` — assign an async callback after construction to be told when a source
    hits a failure retrying cannot fix, so the app can swap in a working one mid-session
  - `quote_staleness_limit` — how long a quote of this source's may go unrefreshed before it must
    not be filled against. Only the source knows: the simulator writes every 0.5 s and Massive
    every 15 s, so a single constant would either refuse valid Massive trades or let the simulator
    freeze for a minute unnoticed

  `start()` does **not** promise any ticker got a price — a transient fetch failure is worth
  retrying, not aborting. Verify by reading the cache; `start_market_data` already does.

  **`start()` also stamps `quote_staleness_limit` onto the cache it is about to write.** A new
  source must do the same. Doing it there rather than at the call sites is what keeps failover
  correct — the lifespan's failover handler installs a simulator directly, and it would otherwise
  inherit a bound meant for a 15-second poller.

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
| `OPENROUTER_API_KEY` | *(empty)* | Required for live chat. The only variable with no working default; without it `POST /api/chat` is a 503 that says so |
| `LLM_MOCK` | `false` | `true`/`1`/`yes`/`on` selects the deterministic mock — no key, no network |
| `MASSIVE_API_KEY` | *(empty)* | Non-empty after `.strip()` selects Massive; else the simulator |
| `MASSIVE_POLL_INTERVAL` | `15` | Seconds between polls (2-5 on Advanced+) |
| `SIM_UPDATE_INTERVAL` | `0.5` | Simulator tick, seconds. `dt` is derived from it, so `sigma` stays annualised volatility at any rate |
| `SIM_EVENT_PROBABILITY` | `2e-5` | Shock chance per ticker **per tick** — so the per-hour shock rate scales with `SIM_UPDATE_INTERVAL`; the ~1-per-session figure assumes the 0.5 s default |

Within the market subsystem, `factory.py` is the only module that reads the
environment. Blank or malformed numeric values fall back to the default with a
warning rather than crashing startup.

`app/config.py` loads `<repo>/.env` at `app.main` import, before
`logging.basicConfig` and before anything reads a variable. **The environment
always wins over the file** (`override=False`), so Docker's `--env-file`, a
shell export and a test's `monkeypatch.setenv` all beat a checked-out `.env`.

Because that load happens at *import*, `monkeypatch` cannot undo it —
`tests/conftest.py` therefore clears every application variable for every test,
from an exhaustive list. A new variable that is not added to `_APP_ENV_VARS` is
one whose value the suite inherits from whoever is running it.

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

No test reaches the network: `OPENROUTER_API_KEY` and `LLM_MOCK` are both
cleared, so `complete()` raises before it can open a socket. A test that wants
the mock takes the `mock_llm` fixture; one that wants to script the model takes
`stub_model`, which patches `app.chat.complete` so the *real* parser still runs.

Gate 3's two harnesses live in `test/`:

```bash
test/smoke.sh                # every exit criterion against a real server
LIVE_LLM=1 test/smoke.sh     # ...including one live OpenRouter call
test/smoke_docker.sh         # the container: clean-clone build, scripts, volume, shutdown
test/e2e.sh 3                # the §12 browser scenarios, three consecutive runs
python3 test/mutate.py       # mutation testing, in a throwaway git worktree
```

The end-to-end suite drives the **built image**, not a dev server, and needs no
key: `test/docker-compose.test.yml` runs it with `LLM_MOCK=true` and the market
simulator. It is the only place the frontend and backend are tested together,
so a change to a response shape that both sides agree on wrongly fails here and
nowhere else.

`tests/test_packaging.py` asserts the Dockerfile, compose file and scripts against each other —
that `.env` cannot enter the build context, that `DB_PATH` stays inside the volume mount, and that
both front doors name the same volume. Those failures are silent: the image still builds and the
app still runs. Change any of those files and run it.

## Demo

```bash
uv run market_data_demo.py   # Live terminal dashboard with simulated prices
```
