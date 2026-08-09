# FinAlly — AI Trading Workstation

## Project Specification

## 1. Vision

FinAlly (Finance Ally) is a visually stunning AI-powered trading workstation that streams live market data, lets users trade a simulated portfolio, and integrates an LLM chat assistant that can analyze positions and execute trades on the user's behalf. It looks and feels like a modern Bloomberg terminal with an AI copilot.

This is the capstone project for an agentic AI coding course. It is built entirely by Coding Agents demonstrating how orchestrated AI agents can produce a production-quality full-stack application. Agents interact through files in `planning/`.

## 2. User Experience

### First Launch

The user runs a single Docker command (or a provided start script). A browser opens to `http://localhost:8000`. No login, no signup. They immediately see:

- A watchlist of 10 default tickers with live-updating prices in a grid
- $10,000 in virtual cash
- A dark, data-rich trading terminal aesthetic
- An AI chat panel ready to assist

### What the User Can Do

- **Watch prices stream** — prices flash green (uptick) or red (downtick) with subtle CSS animations that fade
- **View sparkline mini-charts** — price action beside each ticker in the watchlist, accumulated on the frontend from the SSE stream since page load (sparklines fill in progressively)
- **Click a ticker** to see a larger detailed chart in the main chart area
- **Buy and sell shares** — market orders only, instant fill at current price, no fees, no confirmation dialog
- **Monitor their portfolio** — a heatmap (treemap) showing positions sized by weight and colored by P&L, plus a P&L chart tracking total portfolio value over time
- **View a positions table** — ticker, quantity, average cost, current price, unrealized P&L, % change
- **Chat with the AI assistant** — ask about their portfolio, get analysis, and have the AI execute trades and manage the watchlist through natural language
- **Manage the watchlist** — add/remove tickers manually or via the AI chat

### Visual Design

- **Dark theme**: backgrounds around `#0d1117` or `#1a1a2e`, muted gray borders, no pure black
- **Price flash animations**: brief green/red background highlight on price change, fading over ~500ms via CSS transitions
- **Connection status indicator**: a small colored dot (green = connected, yellow = reconnecting, red = disconnected) visible in the header
- **Professional, data-dense layout**: inspired by Bloomberg/trading terminals — every pixel earns its place
- **Responsive but desktop-first**: optimized for wide screens, functional on tablet

### Color Scheme
- Accent Yellow: `#ecad0a`
- Blue Primary: `#209dd7`
- Purple Secondary: `#753991` (submit buttons)

## 3. Architecture Overview

### Single Container, Single Port

```
┌─────────────────────────────────────────────────┐
│  Docker Container (port 8000)                   │
│                                                 │
│  FastAPI (Python/uv)                            │
│  ├── /api/*          REST endpoints             │
│  ├── /api/stream/*   SSE streaming              │
│  └── /*              Static file serving         │
│                      (Next.js export)            │
│                                                 │
│  SQLite database (volume-mounted)               │
│  Background task: market data polling/sim        │
└─────────────────────────────────────────────────┘
```

- **Frontend**: Next.js with TypeScript, built as a static export (`output: 'export'`), served by FastAPI as static files
- **Backend**: FastAPI (Python), managed as a `uv` project
- **Database**: SQLite, single file at `db/finally.db`, volume-mounted for persistence
- **Real-time data**: Server-Sent Events (SSE) — simpler than WebSockets, one-way server→client push, works everywhere
- **AI integration**: LiteLLM → OpenRouter (Cerebras for fast inference), with structured outputs for trade execution
- **Market data**: Environment-variable driven — simulator by default, real data via Massive API if key provided

### Why These Choices

| Decision | Rationale |
|---|---|
| SSE over WebSockets | One-way push is all we need; simpler, no bidirectional complexity, universal browser support |
| Static Next.js export | Single origin, no CORS issues, one port, one container, simple deployment |
| SQLite over Postgres | No auth = no multi-user = no need for a database server; self-contained, zero config |
| Single Docker container | Students run one command; no docker-compose for production, no service orchestration |
| uv for Python | Fast, modern Python project management; reproducible lockfile; what students should learn |
| Market orders only | Eliminates order book, limit order logic, partial fills — dramatically simpler portfolio math |

---

## 4. Directory Structure

```
finally/
├── frontend/                 # Next.js TypeScript project (static export)
├── backend/                  # FastAPI uv project (Python)
│   └── db/                   # Schema definitions, seed data, migration logic
├── planning/                 # Project-wide documentation for agents
│   ├── PLAN.md               # This document
│   └── ...                   # Additional agent reference docs
├── scripts/
│   ├── start_mac.sh          # Launch Docker container (macOS/Linux)
│   ├── stop_mac.sh           # Stop Docker container (macOS/Linux)
│   ├── start_windows.ps1     # Launch Docker container (Windows PowerShell)
│   └── stop_windows.ps1      # Stop Docker container (Windows PowerShell)
├── test/                     # Playwright E2E tests + docker-compose.test.yml
├── db/                       # Volume mount target (SQLite file lives here at runtime)
│   └── .gitkeep              # Directory exists in repo; finally.db is gitignored
├── Dockerfile                # Multi-stage build (Node → Python)
├── docker-compose.yml        # Optional convenience wrapper
├── .env                      # Environment variables (gitignored, .env.example committed)
└── .gitignore
```

### Key Boundaries

- **`frontend/`** is a self-contained Next.js project. It knows nothing about Python. It talks to the backend via `/api/*` endpoints and `/api/stream/*` SSE endpoints. Internal structure is up to the Frontend Engineer agent.
- **`backend/`** is a self-contained uv project with its own `pyproject.toml`. It owns all server logic including database initialization, schema, seed data, API routes, SSE streaming, market data, and LLM integration. Internal structure is up to the Backend/Market Data agents.
- **`backend/db/`** contains schema SQL definitions and seed logic. The backend lazily initializes the database on first request — creating tables and seeding default data if the SQLite file doesn't exist or is empty.
- **`db/`** at the top level is the runtime volume mount point. The SQLite file (`db/finally.db`) is created here by the backend and persists across container restarts via Docker volume.
- **`planning/`** contains project-wide documentation, including this plan. All agents reference files here as the shared contract.
- **`test/`** contains Playwright E2E tests and supporting infrastructure (e.g., `docker-compose.test.yml`). Unit tests live within `frontend/` and `backend/` respectively, following each framework's conventions.
- **`scripts/`** contains start/stop scripts that wrap Docker commands.

---

## 5. Environment Variables

```bash
# Required: OpenRouter API key for LLM chat functionality
OPENROUTER_API_KEY=your-openrouter-api-key-here

# Optional: Massive (Polygon.io) API key for real market data
# If not set, the built-in market simulator is used (recommended for most users)
MASSIVE_API_KEY=

# Optional: Set to "true" for deterministic mock LLM responses (testing)
LLM_MOCK=false

# Optional: paths, defaulted for local development and overridden in the image
DB_PATH=            # SQLite file. Default <repo>/db/finally.db; the container sets /app/db/finally.db
STATIC_DIR=         # Built frontend. Default backend/static, then frontend/out
LOG_LEVEL=INFO
```

### Behavior

- If `MASSIVE_API_KEY` is set and non-empty → backend uses Massive REST API for market data
- If `MASSIVE_API_KEY` is absent or empty → backend uses the built-in market simulator
- If `LLM_MOCK=true` → backend returns deterministic mock LLM responses (for E2E tests)
- The backend reads `.env` from the project root (mounted into the container or read via docker `--env-file`)
- `DB_PATH`, `STATIC_DIR` and `LOG_LEVEL` are read at call time, not at import, and every one of
  them has a working default — a `.env` with only `OPENROUTER_API_KEY` runs the whole app

---

## 6. Market Data

### Two Implementations, One Interface

Both the simulator and the Massive client implement the same abstract interface. The backend selects which to use based on the environment variable. All downstream code (SSE streaming, price cache, frontend) is agnostic to the source.

### Simulator (Default)

- Generates prices using geometric Brownian motion (GBM) with configurable drift and volatility per ticker
- Updates at ~500ms intervals
- Correlated moves across tickers (e.g., tech stocks move together)
- Occasional random "events" — sudden 2-5% moves on a ticker for drama
- Starts from realistic seed prices (e.g., AAPL ~$190, GOOGL ~$175, etc.)
- Runs as an in-process background task — no external dependencies

### Massive API (Optional)

- REST API polling (not WebSocket) — simpler, works on all tiers
- Polls for the union of all watched tickers on a configurable interval
- Free tier (5 calls/min): poll every 15 seconds
- Paid tiers: poll every 2-15 seconds depending on tier
- Parses REST response into the same format as the simulator

### Shared Price Cache

- A single background task (simulator or Massive poller) writes to an in-memory price cache
- The cache holds the latest price, previous price, and timestamp for each ticker
- SSE streams read from this cache and push updates to connected clients
- This architecture supports future multi-user scenarios without changes to the data layer

### SSE Streaming

- Endpoint: `GET /api/stream/prices`
- Long-lived SSE connection; client uses native `EventSource` API
- Server pushes price updates for all tickers known to the system at a regular cadence (~500ms) — in the single-user model this is equivalent to the user's watchlist
- Each SSE event contains ticker, price, previous price, timestamp, and change direction
- Client handles reconnection automatically (EventSource has built-in retry)

---

## 7. Database

### SQLite with Lazy Initialization

The backend checks for the SQLite database on startup (or first request). If the file doesn't exist or tables are missing, it creates the schema and seeds default data. This means:

- No separate migration step
- No manual database setup
- Fresh Docker volumes start with a clean, seeded database automatically

### Schema

All tables include a `user_id` column defaulting to `"default"`. This is hardcoded for now (single-user) but enables future multi-user support without schema migration.

**users_profile** — User state (cash balance)
- `id` TEXT PRIMARY KEY (default: `"default"`)
- `cash_balance` REAL (default: `10000.0`)
- `created_at` TEXT (ISO timestamp)

**watchlist** — Tickers the user is watching
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT
- `added_at` TEXT (ISO timestamp)
- UNIQUE constraint on `(user_id, ticker)`

**positions** — Current holdings (one row per ticker per user)
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT
- `quantity` REAL (fractional shares supported)
- `avg_cost` REAL
- `updated_at` TEXT (ISO timestamp)
- UNIQUE constraint on `(user_id, ticker)`

**trades** — Trade history (append-only log)
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT
- `side` TEXT (`"buy"` or `"sell"`)
- `quantity` REAL (fractional shares supported)
- `price` REAL
- `executed_at` TEXT (ISO timestamp)

**portfolio_snapshots** — Portfolio value over time (for P&L chart). Recorded every 30 seconds by a background task, and immediately after each trade execution.
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `total_value` REAL
- `recorded_at` TEXT (ISO timestamp)

**chat_messages** — Conversation history with LLM
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `role` TEXT (`"user"` or `"assistant"`)
- `content` TEXT
- `actions` TEXT (JSON — trades executed, watchlist changes made; null for user messages)
- `created_at` TEXT (ISO timestamp)

### Default Seed Data

- One user profile: `id="default"`, `cash_balance=10000.0`
- Ten watchlist entries: AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX

---

## 8. API Endpoints

### Market Data
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/stream/prices` | SSE stream of live price updates |

### Portfolio
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/portfolio` | Current positions, cash balance, total value, unrealized P&L |
| POST | `/api/portfolio/trade` | Execute a trade: `{ticker, quantity, side}` |
| GET | `/api/portfolio/history` | Portfolio value snapshots over time (for P&L chart) |

### Watchlist
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/watchlist` | Current watchlist tickers with latest prices |
| POST | `/api/watchlist` | Add a ticker: `{ticker}` |
| DELETE | `/api/watchlist/{ticker}` | Remove a ticker |

### Chat
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/chat` | Send a message, receive complete JSON response (message + executed actions) |

### System
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check (for Docker/deployment) |

---

## 9. LLM Integration

When writing code to make calls to LLMs, use cerebras-inference skill to use LiteLLM via OpenRouter to the `openrouter/openai/gpt-oss-120b` model with Cerebras as the inference provider. Structured Outputs should be used to interpret the results.

There is an OPENROUTER_API_KEY in the .env file in the project root.

### How It Works

When the user sends a chat message, the backend:

1. Loads the user's current portfolio context (cash, positions with P&L, watchlist with live prices, total portfolio value)
2. Loads recent conversation history from the `chat_messages` table
3. Constructs a prompt with a system message, portfolio context, conversation history, and the user's new message
4. Calls the LLM via LiteLLM → OpenRouter, requesting structured output, using the cerebras-inference skill
5. Parses the complete structured JSON response
6. Auto-executes any trades or watchlist changes specified in the response
7. Stores the message and executed actions in `chat_messages`
8. Returns the complete JSON response to the frontend (no token-by-token streaming — Cerebras inference is fast enough that a loading indicator is sufficient)

### Structured Output Schema

The LLM is instructed to respond with JSON matching this schema:

```json
{
  "message": "Your conversational response to the user",
  "trades": [
    {"ticker": "AAPL", "side": "buy", "quantity": 10}
  ],
  "watchlist_changes": [
    {"ticker": "PYPL", "action": "add"}
  ]
}
```

- `message` (required): The conversational text shown to the user
- `trades` (optional): Array of trades to auto-execute. Each trade goes through the same validation as manual trades (sufficient cash for buys, sufficient shares for sells)
- `watchlist_changes` (optional): Array of watchlist modifications

### Auto-Execution

Trades specified by the LLM execute automatically — no confirmation dialog. This is a deliberate design choice:
- It's a simulated environment with fake money, so the stakes are zero
- It creates an impressive, fluid demo experience
- It demonstrates agentic AI capabilities — the core theme of the course

If a trade fails validation (e.g., insufficient cash), the error is included in the chat response so the LLM can inform the user.

### System Prompt Guidance

The LLM should be prompted as "FinAlly, an AI trading assistant" with instructions to:
- Analyze portfolio composition, risk concentration, and P&L
- Suggest trades with reasoning
- Execute trades when the user asks or agrees
- Manage the watchlist proactively
- Be concise and data-driven in responses
- Always respond with valid structured JSON

### LLM Mock Mode

When `LLM_MOCK=true`, the backend returns deterministic mock responses instead of calling OpenRouter. This enables:
- Fast, free, reproducible E2E tests
- Development without an API key
- CI/CD pipelines

---

## 10. Frontend Design

### Layout

The frontend is a single-page application with a dense, terminal-inspired layout. The specific component architecture and layout system is up to the Frontend Engineer, but the UI should include these elements:

- **Watchlist panel** — grid/table of watched tickers with: ticker symbol, current price (flashing green/red on change), daily change %, and a sparkline mini-chart (accumulated from SSE since page load)
- **Main chart area** — larger chart for the currently selected ticker, with at minimum price over time. Clicking a ticker in the watchlist selects it here.
- **Portfolio heatmap** — treemap visualization where each rectangle is a position, sized by portfolio weight, colored by P&L (green = profit, red = loss)
- **P&L chart** — line chart showing total portfolio value over time, using data from `portfolio_snapshots`
- **Positions table** — tabular view of all positions: ticker, quantity, avg cost, current price, unrealized P&L, % change
- **Trade bar** — simple input area: ticker field, quantity field, buy button, sell button. Market orders, instant fill.
- **AI chat panel** — docked/collapsible sidebar. Message input, scrolling conversation history, loading indicator while waiting for LLM response. Trade executions and watchlist changes shown inline as confirmations.
- **Header** — portfolio total value (updating live), connection status indicator, cash balance

### Technical Notes

- Use `EventSource` for SSE connection to `/api/stream/prices`
- Canvas-based charting library preferred (Lightweight Charts or Recharts) for performance
- Price flash effect: on receiving a new price, briefly apply a CSS class with background color transition, then remove it
- All API calls go to the same origin (`/api/*`) — no CORS configuration needed
- Tailwind CSS for styling with a custom dark theme

---

## 11. Docker & Deployment

### Multi-Stage Dockerfile

```
Stage 1: Node 20 slim
  - Copy frontend/
  - npm install && npm run build (produces static export)

Stage 2: Python 3.12 slim
  - Install uv
  - Copy backend/
  - uv sync (install Python dependencies from lockfile)
  - Copy frontend build output into a static/ directory
  - Expose port 8000
  - CMD: uvicorn serving FastAPI app
```

FastAPI serves the static frontend files and all API routes on port 8000.

### Docker Volume

The SQLite database persists via a named Docker volume:

```bash
docker run -v finally-data:/app/db -p 8000:8000 --env-file .env finally
```

The `db/` directory in the project root maps to `/app/db` in the container. The backend writes `finally.db` to this path.

### Start/Stop Scripts

**`scripts/start_mac.sh`** (macOS/Linux):
- Builds the Docker image if not already built (or if `--build` flag passed)
- Runs the container with the volume mount, port mapping, and `.env` file
- Prints the URL to access the app
- Optionally opens the browser

**`scripts/stop_mac.sh`** (macOS/Linux):
- Stops and removes the running container
- Does NOT remove the volume (data persists)

**`scripts/start_windows.ps1`** / **`scripts/stop_windows.ps1`**: PowerShell equivalents for Windows.

All scripts should be idempotent — safe to run multiple times.

### Optional Cloud Deployment

The container is designed to deploy to AWS App Runner, Render, or any container platform. A Terraform configuration for App Runner may be provided in a `deploy/` directory as a stretch goal, but is not part of the core build.

---

## 12. Testing Strategy

### Unit Tests (within `frontend/` and `backend/`)

**Backend (pytest)**:
- Market data: simulator generates valid prices, GBM math is correct, Massive API response parsing works, both implementations conform to the abstract interface
- Portfolio: trade execution logic, P&L calculations, edge cases (selling more than owned, buying with insufficient cash, selling at a loss)
- LLM: structured output parsing handles all valid schemas, graceful handling of malformed responses, trade validation within chat flow
- API routes: correct status codes, response shapes, error handling

**Frontend (React Testing Library or similar)**:
- Component rendering with mock data
- Price flash animation triggers correctly on price changes
- Watchlist CRUD operations
- Portfolio display calculations
- Chat message rendering and loading state

### E2E Tests (in `test/`)

**Infrastructure**: A separate `docker-compose.test.yml` in `test/` that spins up the app container plus a Playwright container. This keeps browser dependencies out of the production image.

**Environment**: Tests run with `LLM_MOCK=true` by default for speed and determinism.

**Key Scenarios**:
- Fresh start: default watchlist appears, $10k balance shown, prices are streaming
- Add and remove a ticker from the watchlist
- Buy shares: cash decreases, position appears, portfolio updates
- Sell shares: cash increases, position updates or disappears
- Portfolio visualization: heatmap renders with correct colors, P&L chart has data points
- AI chat (mocked): send a message, receive a response, trade execution appears inline
- SSE resilience: disconnect and verify reconnection

---

## 13. Build Checkpoints

The sections above describe the *what*. This section describes the *order* — the project broken into
ten checkpoints, each one a self-contained slice of work that ends in a demonstrable, verifiable
state. Agents pick up the lowest-numbered incomplete checkpoint.

### Definition of Done (applies to every checkpoint)

Every checkpoint passes through three gates, **in order**. No gate may be skipped, and no checkpoint
is marked ✅ until all three have passed. Work does not begin on checkpoint N+1 while N sits at an
unpassed gate.

#### Gate 1 — Test

1. Every exit criterion listed for the checkpoint passes, verified by actually running the command —
   not by reading the code and concluding it should work
2. Unit tests covering the new code exist and pass (`uv run --extra dev pytest` for backend,
   `npm test` for frontend)
3. The **full** suite is green, not just the new tests — nothing from an earlier checkpoint regressed
4. Linting is clean (`ruff check app/ tests/`; `npm run lint` and `tsc --noEmit` for frontend)
5. Coverage has not dropped below the previous checkpoint's figure; record the new figure in the
   status table

Tests must be capable of failing for the right reason. A test that passes against a deliberately
broken implementation is not coverage — see Checkpoint 1, where thirteen `MagicMock`-based tests
passed against a client that could never populate the cache.

#### Gate 2 — Review

Only once Gate 1 is green, and before merge:

1. Run `/code-review high` over the branch diff. Every **CONFIRMED** correctness finding is fixed;
   every **PLAUSIBLE** one is either fixed or answered in writing on the PR — silently ignoring one
   is not an outcome
2. Run `/security-review` on any checkpoint handling untrusted input, money movement, or secrets.
   Required for Checkpoints 3, 4, and 8; optional elsewhere
3. Apply fixes, then **re-run Gate 1 in full**. Review fixes are code changes and break things like
   any other
4. Open a PR to `main`. `.github/workflows/claude-code-review.yml` reviews it automatically on open
   and on every push. Unresolved review comments block merge
5. Run `/simplify` on the diff before requesting merge, so the accumulated code stays legible to the
   agents working on later checkpoints

What each checkpoint's review should weight most heavily:

| # | Review focus |
|---|---|
| 1 | Do the new tests fail when the extraction ladder, log-space shocks, or failover are reverted? |
| 2 | Lazy DB init idempotency and concurrent first-request races; lifespan shutdown leaks |
| 3 | Trade validation and money maths — rounding, fractional shares, avg-cost drift, oversell |
| 4 | Untrusted LLM output reaching the trade path; prompt-injection via chat; key handling |
| 5 | `EventSource` lifecycle — leaked connections, listeners, and timers across reconnects |
| 6 | Render performance under a 500 ms tick; chart teardown on unmount |
| 7 | Auto-executed actions rendered honestly, including partial and failed ones |
| 8 | Secrets and `.env` not baked into the image; volume permissions; image size |
| 9 | Flaky-test sources — fixed sleeps, unpinned versions, order dependence |
| 10 | Stale claims in docs; dead code and abandoned scaffolding |

#### Gate 3 — Record

**This document is the project's record. It is updated as part of every checkpoint, never
retroactively in a batch at the end.**

1. Update the checkpoint's row in the status table — gates and coverage — in the same commit as the
   work
2. Append a **checkpoint log entry** (template and log below) recording what was actually built. Not
   what was planned — what exists on disk when the checkpoint closed
3. If the implementation diverged from the design, correct the affected section of this document and
   any relevant `planning/` doc, so the spec describes the built system. An undocumented divergence
   is how `MARKET_DATA_SUMMARY.md` came to describe a subsystem with a blocking bug as "Complete,
   tested, reviewed"
4. Branch name `checkpoint-N-<slug>`; merge to `main` only after Gates 1 and 2 have both passed

Items 1–3 land in the same commit as the code, not a follow-up commit. A green checkpoint with a
stale `PLAN.md` has not passed Gate 3.

If an exit criterion or a gate cannot be met, do not mark the checkpoint done and move on. Set the
row to ⛔, write a log entry describing how far it got and what blocked it, and raise it.

### Status

| # | Checkpoint | Depends on | G1 Test | G2 Review | G3 Record | Coverage | Status |
|---|---|---|---|---|---|---|---|
| 1 | Market data hardening | — | ✅ | ✅ | ✅ | 100% | ✅ Complete (PR #4) |
| 2 | Backend skeleton + database | 1 | ✅ | 🔨 | 🔨 | 100% | 🔨 In progress |
| 3 | Portfolio & watchlist API | 2 | ⬜ | ⬜ | ⬜ | — | ⬜ Not started |
| 4 | LLM chat integration | 3 | ⬜ | ⬜ | ⬜ | — | ⬜ Not started |
| 5 | Frontend scaffold + live prices | 2 | ⬜ | ⬜ | ⬜ | — | ⬜ Not started |
| 6 | Charts, portfolio visualisation, trade bar | 3, 5 | ⬜ | ⬜ | ⬜ | — | ⬜ Not started |
| 7 | Chat panel | 4, 6 | ⬜ | ⬜ | ⬜ | — | ⬜ Not started |
| 8 | Docker packaging + start/stop scripts | 7 | ⬜ | ⬜ | ⬜ | — | ⬜ Not started |
| 9 | End-to-end test suite | 8 | ⬜ | ⬜ | ⬜ | — | ⬜ Not started |
| 10 | Polish, docs, and release readiness | 9 | ⬜ | ⬜ | ⬜ | — | ⬜ Not started |

Legend: ⬜ not started · 🔨 in progress · ✅ complete · ⛔ blocked

A checkpoint's Status may only read ✅ when G1, G2, and G3 all read ✅. Coverage records the backend
figure at Gate 1, and must not fall from one checkpoint to the next.

Checkpoints 5 and 2 unblock in parallel — frontend work can begin against the SSE endpoint as soon
as the backend skeleton serves it, without waiting for the portfolio API.

### Already built (pre-checkpoint baseline)

`backend/app/market/` — the eight-module market data subsystem (models, cache, interface, seed
prices, GBM simulator, Massive REST client, factory, SSE router) plus 73 unit tests. See
`planning/MARKET_DATA_SUMMARY.md`. This is the starting point, not a finished component:
Checkpoint 1 exists because `planning/MARKET_DATA_DESIGN.md` §17 identifies defects in it.

---

### Checkpoint 1 — Market data hardening

**Goal:** bring `backend/app/market/` up to the target design before anything is built on top of it.
Every later checkpoint reads prices from this layer, so its defects would propagate everywhere.

**Scope:** the 17 changes in `planning/MARKET_DATA_DESIGN.md` §17. The highest-consequence ones:

- The Massive client reads `snap.last_trade.timestamp`, which does not exist. Every snapshot raises
  `AttributeError`, is swallowed, and skipped — with a real API key the cache stays permanently
  empty while the app reports healthy. Replace with the `extract_price` / `extract_timestamp` ladder
- Rebuild the Massive test fixtures from `TickerSnapshot.from_dict(...)`. `MagicMock` fabricates any
  attribute, which is why thirteen passing tests missed the bug above
- Drop `event_probability` to `2e-5` and apply shocks in log space, so `TICKER_PARAMS` volatility
  is actually what the simulator produces
- Add `start_market_data()` with fail-fast-then-fall-back-to-simulator, and classify permanent
  (401/403) versus transient failures
- Add `previous_close` to `PriceUpdate`, `MarketEvent` + `EventLog`, SSE heartbeats, and
  `event: shock` / `event: status` frames

**Exit criteria:**

- A Massive test built from a recorded `TickerSnapshot.from_dict(...)` payload asserts the cache is
  populated — and fails if the extraction ladder is reverted
- Contract tests run against both `SimulatorDataSource` and `MassiveDataSource` and pass for both
- An invalid `MASSIVE_API_KEY` logs a fallback and yields a running simulator, not an empty cache
- A 30-second simulator run produces per-ticker realised volatility within a factor of two of the
  configured `TICKER_PARAMS` value
- `uv run --extra dev pytest` green; backend coverage ≥ 85% on `app/market/`

---

### Checkpoint 2 — Backend skeleton + database

**Goal:** a running FastAPI application with a live SQLite database and a streaming price endpoint.

**Scope:**

- `app/main.py` — FastAPI app, lifespan handler that calls `start_market_data()` on startup and
  stops the source on shutdown, mounts `create_stream_router()`
- `app/db/` — `schema.sql` for the six tables in §7, a connection helper, and lazy initialisation
  that creates and seeds on first use when the file is absent or the tables are missing
- Seed data: the default profile at $10,000 and the ten default watchlist tickers
- `GET /api/health`
- Static file mount for the frontend export, tolerating an absent directory in local dev

**Exit criteria:**

- `uv run uvicorn app.main:app` starts and `GET /api/health` returns 200
- Deleting `db/finally.db` and issuing any request recreates it with all six tables and the seed
  rows — verified twice in a row, proving the path is genuinely idempotent
- `curl -N localhost:8000/api/stream/prices` emits price frames within two seconds and a heartbeat
  during an idle period
- Watchlist tickers seeded in the database are the tickers the market data source was started with

---

### Checkpoint 3 — Portfolio & watchlist API

**Goal:** the complete trading backend, so the frontend has real state to render.

**Scope:**

- `GET /api/portfolio` — positions with live marks, cash, total value, unrealised P&L
- `POST /api/portfolio/trade` — validate, fill at the cached price, update position and cash, append
  to `trades`, write a snapshot
- `GET /api/portfolio/history` — snapshots for the P&L chart
- `GET`/`POST`/`DELETE` watchlist, each mutation also calling `add_ticker()` / `remove_ticker()` on
  the live source so a newly added ticker starts streaming immediately
- Background task writing a `portfolio_snapshots` row every 30 seconds

**Exit criteria:**

- Unit tests cover: buy with insufficient cash rejected; sell of more shares than held rejected;
  fractional quantities; weighted average cost after successive buys; average cost unchanged by a
  sell; position row removed at quantity zero; zero and negative quantities rejected
- Adding a ticker via `POST /api/watchlist` makes it appear in the SSE stream without a restart
- Removing a ticker held as a position does not delete the position
- A trade writes a snapshot immediately, so the P&L chart has a point at the trade time
- Every endpoint returns the documented shape and correct status codes, including the error paths

---

### Checkpoint 4 — LLM chat integration

**Goal:** `POST /api/chat` — the agentic core of the product.

**Scope:**

- LiteLLM → OpenRouter `openrouter/openai/gpt-oss-120b` with Cerebras as provider, via the
  `cerebras` skill; structured outputs for the §9 schema
- Portfolio context assembly, conversation history from `chat_messages`, system prompt per §9
- Auto-execution of returned trades and watchlist changes through the *same* validation path as
  Checkpoint 3 — no second implementation of trade logic
- Per-action results fed back into the response so the user sees which actions succeeded or failed
- `LLM_MOCK=true` deterministic mock path

**Exit criteria:**

- With `LLM_MOCK=true`, a chat request returns a schema-valid response and a mocked trade actually
  moves cash and positions
- A malformed or non-JSON model response produces a graceful error message to the user, never a 500
- A trade the LLM requests that fails validation returns its error in the response rather than
  silently vanishing
- Messages and their actions persist to `chat_messages` and are replayed as history on the next call
- One live (non-mocked) call succeeds against OpenRouter, confirming model id, provider routing, and
  structured-output handling

---

### Checkpoint 5 — Frontend scaffold + live prices

**Goal:** the terminal shell, streaming.

**Scope:**

- Next.js + TypeScript configured for `output: 'export'`, Tailwind with the §2 dark theme and the
  three brand colours
- `EventSource` hook for `/api/stream/prices` with reconnection state
- Watchlist panel: symbol, price, daily change %, price flash on change, sparkline accumulated
  client-side from the stream
- Header with portfolio total, cash, and the connection status dot

**Exit criteria:**

- `npm run build` produces a static export in `out/` with no errors
- Prices visibly stream and flash green/red, the flash fading rather than sticking
- Stopping the backend turns the dot yellow then red; restarting it reconnects without a page reload
- Sparklines accumulate progressively from page load rather than rendering empty or fabricated data
- Component tests cover render-with-mock-data and that a flash class is applied on price change

---

### Checkpoint 6 — Charts, portfolio visualisation, trade bar

**Goal:** the data-dense workstation of §10.

**Scope:** main chart for the selected ticker, portfolio treemap heatmap, P&L line chart from
`/api/portfolio/history`, positions table, and the trade bar.

**Exit criteria:**

- Clicking a watchlist row selects that ticker in the main chart
- A buy from the trade bar updates cash, positions table, heatmap, and header total with no reload
- The heatmap sizes by portfolio weight and colours by P&L sign, and survives an empty portfolio
- The P&L chart renders the snapshot series and extends as new snapshots arrive
- A rejected trade surfaces a visible error instead of failing silently

---

### Checkpoint 7 — Chat panel

**Goal:** the AI copilot, wired end to end.

**Scope:** docked collapsible sidebar, scrolling history, loading indicator, inline rendering of
executed trades and watchlist changes.

**Exit criteria:**

- Sending a message shows a loading indicator, then the response
- An LLM-executed trade appears inline as a confirmation *and* is reflected in the portfolio panels
- An LLM watchlist addition appears in the watchlist and begins streaming
- History survives a page reload
- The panel collapses and expands without disturbing the rest of the layout

---

### Checkpoint 8 — Docker packaging + start/stop scripts

**Goal:** the single-command launch promised in §2.

**Scope:** the multi-stage Dockerfile of §11, `docker-compose.yml`, and the four start/stop scripts.

**Exit criteria:**

- `docker build .` succeeds from a clean clone with no local Node or Python toolchain assumed
- The running container serves both the UI and the API on port 8000
- A trade survives `stop_mac.sh` followed by `start_mac.sh` — the named volume genuinely persists
- Running each script twice in a row is safe and produces no error
- The container starts and functions with `MASSIVE_API_KEY` empty, and with no `.env` beyond
  `OPENROUTER_API_KEY`

---

### Checkpoint 9 — End-to-end test suite

**Goal:** the §12 scenarios, automated.

**Scope:** `test/docker-compose.test.yml` bringing up the app container plus Playwright, and specs
covering every scenario listed in §12, running with `LLM_MOCK=true`.

**Exit criteria:**

- `docker compose -f test/docker-compose.test.yml up --abort-on-container-exit` exits zero
- Every §12 scenario has a spec: fresh start, watchlist add/remove, buy, sell, portfolio
  visualisation, mocked chat with trade execution, and SSE reconnection
- The suite passes three consecutive runs — flaky streaming assertions are failures, not noise
- No test depends on a real OpenRouter or Massive key

---

### Checkpoint 10 — Polish, docs, and release readiness

**Goal:** a project someone else can clone and run.

**Scope:** README quickstart, `.env.example`, a summary doc for each subsystem matching the form of
`MARKET_DATA_SUMMARY.md`, and a final visual pass against the §2 design intent.

**Exit criteria:**

- A clean clone, following only the README, reaches a working app at `localhost:8000`
- `.env.example` documents every variable in §5 with safe defaults
- Backend coverage ≥ 80%; `ruff`, `tsc --noEmit`, and `npm run lint` all clean
- The UI matches §2: dark theme, brand colours, dense layout, no placeholder text or lorem ipsum
- `planning/` docs describe what was actually built, with no stale claims left behind
- The checkpoint log below has an entry for all ten checkpoints, and each one matches the code

---

### Checkpoint log

Append-only. One entry per checkpoint, written at Gate 3, in the same commit as the code. Newest
last. An agent picking up this project should be able to read the log and know the true state of the
build without running anything.

Write it as a record, not a status report: what exists, what was cut, what is known to be wrong.
Anything a later checkpoint will trip over belongs in **Carried forward**, and every carried-forward
item must be resolved or restated in a later entry — it does not expire by being ignored.

#### Entry template

```markdown
#### Checkpoint N — <name>

- **Closed:** YYYY-MM-DD · branch `checkpoint-N-<slug>` · PR #NN
- **Built:** the modules, endpoints, and components that now exist, by path
- **Exit criteria:** each one, with the command run and its result. Any not met, and why
- **Tests:** counts added and total, full-suite result, backend coverage before → after
- **Review:** `/code-review` findings by verdict, what was fixed, and the written disposition of
  anything not fixed. `/security-review` result where required
- **Diverged from plan:** what was built differently from the spec above, and why. "Nothing" is a
  valid and expected answer — say it explicitly rather than omitting the field
- **Carried forward:** known gaps, deferred work, and anything the next checkpoint must know
```

#### Entries

#### Checkpoint 1 — Market data hardening

- **Closed:** 2026-08-09 · branch `checkpoint-1-market-data-hardening` · PR #4, squash-merged to
  `main` · all three gates passed. The automated review workflow ran on each push and raised nothing
  ("No buffered inline comments" on every run)
- **Built:** all 17 changes from `MARKET_DATA_DESIGN.md` §17, across `backend/app/market/`
  - `models.py` — `normalize_ticker()`, `previous_close` + `day_change`/`day_change_percent` on
    `PriceUpdate`, new `MarketEvent`
  - `events.py` — **new**: `EventLog`, a bounded ring buffer with per-client cursors
  - `cache.py` — normalisation at every entry point, sticky `previous_close`, `is None` timestamp
    check, `version` read under the lock
  - `interface.py` — `PermanentMarketDataError`
  - `simulator.py` — injected RNGs, log-space shocks, `event_probability` 0.001 → 2e-5,
    session-open baseline, `drain_events()`, Cholesky `LinAlgError` degradation
  - `massive_client.py` — the `extract_price`/`extract_timestamp`/`extract_previous_close` ladder,
    permanent-vs-transient classification, 5 s timeouts, `get_market_status()` polling
  - `factory.py` — `start_market_data()` with fail-fast-then-fallback, `MASSIVE_POLL_INTERVAL` /
    `SIM_UPDATE_INTERVAL` / `SIM_EVENT_PROBABILITY`
  - `stream.py` — router built inside the factory, heartbeat comments, `event: shock` and
    `event: status` frames
- **Exit criteria:**
  - *Massive fixture test fails if the ladder is reverted* — **verified by mutation.** Restoring
    `snap.last_trade.timestamp / 1000.0` fails 16 tests; the previous `MagicMock` suite passed that
    same mutation. Two further mutations run: removing the Cholesky multiply fails 2 correlation
    tests; switching shocks back to `*= (1 ± m)` fails 2 shock tests
  - *Contract tests pass for both sources* — `test_source_contract.py`, 7 tests × 2 sources
  - *Invalid key yields a running simulator* — `test_falls_back_on_permanent_failure`, plus
    `test_falls_back_when_authenticated_but_no_prices` for the Basic-plan case
  - *Realised volatility within a factor of two of `TICKER_PARAMS`* — measured over a full
    46,800-tick session per ticker, shocks disabled: every ticker lands at **1.00–1.07×** its
    configured sigma (AAPL 0.230 vs 0.22, TSLA 0.501 vs 0.50, JPM 0.193 vs 0.18)
  - *Suite green, coverage ≥ 85% on `app/market/`* — **228 passed, 100%**
- **Tests:** 73 → **228** (+155). Three consecutive full runs green after every pass, no flakes.
  `ruff check` and `ruff format --check` clean. Coverage 84% → **100%** on `app/market/`, every
  module at 100%. Runtime 6.5 s → **2.0 s**, with no network access at all

  One test of mine was itself wrong and was rewritten: the first log-space-shock test derived the
  magnitude from the result and inverted it, so it passed under `*= (1 ± m)` too. Mutation testing
  is what caught it. A second used `"401"` as an error body — which the corrected classifier rightly
  treats as transient — and hung the suite rather than failing; the permanent-failure loop tests now
  use real bodies and `asyncio.wait_for`, so a regression fails instead of hanging
- **Review:** `/code-review high` returned 8 findings — **7 fixed, 1 deferred with reason.**

  | # | Finding | Disposition |
  |---|---|---|
  | 1 | `is_permanent_failure` classified on the raw body, so `"401"`/`"403"` could never match a real Polygon body (false negative on a dead key) while a random hex `request_id` could match one (false positive on a 429) | **Fixed.** The SDK raises `BadResponse(resp.data.decode())` and discards `resp.status` — verified in `massive/rest/base.py` — so the HTTP code is genuinely unavailable. Now parses the body's `status`/`message`/`error` fields and matches on real wording (`Unknown API Key`, `NOT_AUTHORIZED`); `AuthError` is permanent. Test bodies replaced with real ones |
  | 2 | `on_permanent_failure` accepted but never wired, so mid-run failover doesn't happen | **Deferred to Checkpoint 2.** `MARKET_DATA_DESIGN.md` §11 specifies it is wired in the lifespan (§13), which does not exist until Checkpoint 2. Already listed under Carried forward |
  | 3 | `status_provider()` unguarded, and the wiring documented in `backend/CLAUDE.md` raises `AttributeError` under the default simulator — escaping the generator, aborting the response, and putting `EventSource` into an infinite reconnect loop | **Fixed** in both places: guarded in `stream.py`, and the documented snippet now uses `getattr(source, "market_status", None)` |
  | 4 | `_poll_loop` handled only `PermanentMarketDataError`; anything else silently killed the poller for the life of the process | **Fixed.** Catch-all in the loop, plus per-snapshot guarding in `_poll_once` so one malformed entry costs one ticker for one poll |
  | 5 | Usability check was `len(price_cache) == 0`, which passes vacuously on a shared cache and accepts 1-of-10 tickers priced | **Fixed.** Checks the requested tickers specifically; warns and names the missing ones on partial coverage rather than forcing a fallback |
  | 6 | Fallback simulator ignored `SIM_UPDATE_INTERVAL` / `SIM_EVENT_PROBABILITY` | **Fixed.** Both paths go through one `_create_simulator` helper |
  | 7 | Bare `float(os.environ.get(...))` crashed startup on `MASSIVE_POLL_INTERVAL=` | **Fixed.** `_env_float` falls back to the default with a warning |
  | 8 | `update_interval` not propagated to `dt`, so `SIM_UPDATE_INTERVAL` silently mis-scaled volatility | **Fixed.** `dt` is derived from the tick rate, so `sigma` stays annualised at any interval |

  Fixes verified by mutation in both directions: restoring whole-body matching fails the
  `request_id` test; restoring the original marker list fails 3 tests including a real 401 body.
  `/security-review` not run — optional for this checkpoint per the gate definition.

  `/simplify` (4 agents) then found one thing that mattered and several that tightened the design:

  - **The `[massive]` contract tests were making real HTTPS calls to `api.massive.com`.** Stubbing
    the instance was never enough — `start()` overwrites `_client` with a real `RESTClient` and then
    polls market status. Seven round trips, ~4.5 s of a 6.4 s suite, and the tests failed offline.
    Now patched at the class level: **suite 6.5 s → 2.0 s, zero network access**
  - `market_status` and `on_permanent_failure` moved onto the `MarketDataSource` ABC. Consumers stop
    needing `getattr`, the SSE guard stops being load-bearing, and Checkpoint 2's lifespan will not
    have to reach into a private attribute to wire failover
  - `EventLog.__len__` deleted — no production caller, and its only legacy was the falsy-empty-log
    trap that had already caused one silent bug plus three documented workarounds
  - `start_market_data` now starts and verifies every source identically; `isinstance` guards only
    the recursion. Tuning defaults have one owner each; test builders and real error bodies moved to
    `conftest.py`; `extract_price` reuses `extract_previous_close`
  - The ABC's `start()` docstring promised "populate the cache or raise", which `MassiveDataSource`
    does not honour — which is exactly why the factory re-verifies. Corrected to match reality

  Skipped, with reasons: hoisting the duplicated `stop()` into the ABC (§7 deliberately chose
  contract over inheritance); deleting `_classifiable_text` (it keeps classification off the opaque
  `request_id` by construction, rather than by luck about which markers happen to be hex);
  parametrising the seven pre-existing factory tests (outside this diff).
- **Diverged from plan:** one deliberate divergence. §16.4 specified SSE tests driven through
  `httpx.ASGITransport`; that cannot work, because the SSE generator is infinite by design and
  ASGITransport never delivers an `http.disconnect`, so `request.is_disconnected()` stays False and
  closing the response hangs forever (verified — it blocks before the first frame). `test_stream.py`
  instead drives `_generate_events` directly with a stub request that disconnects after N ticks, and
  asserts the HTTP wiring off the router. Deterministic, no sleeps, and it reaches 97% on a module
  that had none. §16.4 of the design doc has been corrected to match
- **Closing pass (2026-08-09, same branch):** the three loose ends that were CP1's own rather than
  CP2's were closed before merge, taking `app/market/` to **100%** coverage:
  - The two uncovered lines are now tested, and both tests were **mutation-verified**. Removing the
    `CancelledError` handler fails the two new stream tests. The `_refresh_market_status` guard
    needed the stronger assertion: a plain "does not raise" test *survived* removing the guard,
    because the catch-all below swallows the resulting `AttributeError`. It now asserts the silence
    — no "Market status unavailable" log — and that kills the mutation. Note the log entry above
    misdescribed this line as "the `get_market_status` success path"; it is the `self._client is
    None` early return, which is reached when `_poll_loop` refreshes status before `start()`
  - `market_data_demo.py` now consumes `EventLog` instead of its own `abs(change_percent) > 1.0`
    heuristic. That heuristic was effectively dead: tick-over-tick change at 500 ms is ~0.02%, so
    the panel never populated. The demo also raises `event_probability` to `2.5e-3` — at the 2e-5
    production default a 60-second run expects 0.02 shocks. Measured over 30 seeded runs the demo
    now averages **3.2 shocks per run** (min 0, max 6)
  - `httpx` is **kept, not dropped**: starlette's `TestClient` requires it, and Checkpoint 2 needs
    that for `GET /api/health`. Its `pyproject.toml` comment, which still claimed it was for the
    SSE integration tests, has been corrected
- **Carried forward:**
  - `on_permanent_failure` is now a public attribute on the `MarketDataSource` ABC and is
    unit-tested, but nothing assigns it yet — Checkpoint 2's lifespan must, so mid-run failover
    reassigns the active source. **This is review finding #2, deferred rather than resolved; it must
    not be lost**
  - `status_provider` can now be wired as plain `lambda: source.market_status` — `market_status` is
    on the ABC, so the old `getattr` workaround is no longer needed
  - `EventLog` is threaded through simulator → factory → stream, and `market_data_demo.py` now
    constructs one, but no *server* consumer does; Checkpoint 2 must create it in the lifespan and
    pass it to both the source and the stream router
  - `app/market/` is at 100% line coverage. That is a floor to hold, not a target reached: it says
    every line runs, not that every line is pinned. Two of the newest tests only became meaningful
    once mutation testing showed the obvious version passing against broken code
