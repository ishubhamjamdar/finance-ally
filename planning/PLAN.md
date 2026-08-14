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

# Frontend, build-time only. Empty in production: the export and the API share
# an origin. Set it for `next dev`, which serves the UI on :3000 while the API
# is on :8000 — `output: 'export'` ignores rewrites, so there is no dev proxy.
NEXT_PUBLIC_API_BASE=   # e.g. http://localhost:8000
```

### Behavior

- If `MASSIVE_API_KEY` is set and non-empty → backend uses Massive REST API for market data
- If `MASSIVE_API_KEY` is absent or empty → backend uses the built-in market simulator
- If `LLM_MOCK=true` → backend returns deterministic mock LLM responses (for E2E tests)
- The backend reads `.env` from the project root (mounted into the container or read via docker `--env-file`)
- `DB_PATH`, `STATIC_DIR` and `LOG_LEVEL` are read at call time, not at import, and every one of
  them has a working default — a `.env` with only `OPENROUTER_API_KEY` runs the whole app
- `NEXT_PUBLIC_API_BASE` is the one variable the *frontend* reads, and it is **inlined into the
  bundle at build time** rather than read at runtime. Nothing secret may ever go in it

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

**The cache also records when each entry arrived, and a quote that has outlived
its source's cadence cannot be traded on.** A poller that wedges while its
object is still alive leaves every price frozen at its last value, which no
"is there a source?" check can detect — and a fill against a frozen price
records a price the market has moved away from. Each source declares its own
`quote_staleness_limit` (the simulator writes every 0.5 s, Massive every 15 s,
so no single constant works) and stamps it on the cache when it starts writing.

The bound is on **receipt** time, not on `PriceUpdate.timestamp`. That field is
the venue's last trade time, which is hours old the moment the market closes;
bounding it would refuse every trade out of hours. Only trading is refused —
valuation still answers with the last known marks, because a blank portfolio is
worse than a stale one.

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

**portfolio_snapshots** — Portfolio value over time (for P&L chart). Recorded every 30 seconds by a background task, and immediately after each trade execution — **except while a held ticker has no cached price.** A snapshot row carries `total_value` and nothing else, so unlike `GET /api/portfolio` it cannot say "this omits a position"; written anyway it would be a drawdown the account never suffered, permanently on the chart, that later "recovers" when the price returns. A gap in the series is the honest alternative. Ordinary runs are unaffected: a position can only be opened for a ticker that had a price.
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
| Method | Path | Description | Codes |
|--------|------|-------------|-------|
| GET | `/api/portfolio` | Current positions, cash balance, total value, unrealized P&L | 200 |
| POST | `/api/portfolio/trade` | Execute a trade: `{ticker, quantity, side}` | 201 · 400 rejected · 422 malformed · 503 no feed |
| GET | `/api/portfolio/history` | Portfolio value snapshots over time (for P&L chart), oldest first; `?limit=` 1–5000, default 500 | 200 · 422 |

### Watchlist
| Method | Path | Description | Codes |
|--------|------|-------------|-------|
| GET | `/api/watchlist` | Current watchlist tickers with latest prices, in add order | 200 |
| POST | `/api/watchlist` | Add a ticker: `{ticker}` | 201 · 400 list full · 409 duplicate · 422 · 503 no feed |
| DELETE | `/api/watchlist/{ticker}` | Remove a ticker. Does not sell, and keeps a held ticker subscribed | 200 · 404 · 422 · 503 no feed |

**The watchlist holds at most 50 tickers.** Every entry joins every Massive
poll for the life of the process, and Checkpoint 4 hands this endpoint to an
LLM that can call it in a loop. A duplicate is still reported as a duplicate
when the list is full, because re-adding a watched ticker adds nothing to poll.

**400 versus 422.** 422 means the request was malformed — a quantity that is not
a positive finite number, a ticker that is not a symbol, an unexpected field.
400 means it was well formed and the account could not support it: no price yet,
a price that has stopped updating, insufficient cash, selling more than is held.
The frontend renders them differently, and the Checkpoint 4 chat handler has to
tell them apart to report back usefully.

**A frozen price is a 400, not a 503.** 503 is reserved for a feed that is
*gone*; a source that is running but no longer producing answers requests, so
the refusal names the ticker and says what to check. §6 has the reasoning.

**A trade never accepts a price from the client.** The fill price is read from
the server-side cache; the request schema has no `price` field and forbids
unexpected ones, so a request naming its own price is rejected rather than
silently ignored.

### Chat
| Method | Path | Description | Codes |
|--------|------|-------------|-------|
| POST | `/api/chat` | Send a message: `{message}`. Returns the reply, the actions it executed, and the resulting portfolio | 200 · 422 malformed · 503 no feed or no provider |
| GET | `/api/chat/history` | Stored transcript, oldest first; `?limit=` 1–500, default 50 | 200 · 422 |

**A bad *answer* is not an error.** A model that returns unparseable output
produces a 200 whose `message` says the turn went wrong, and an action the
model asked for that fails validation comes back inside `actions` with
`ok: false` and the reason. Only two things are 503: no market data source, and
a provider that could not be reached at all. The distinction is the difference
between "try that again" and "try saying it differently".

**`POST /api/chat` requires a running market source**, exactly as
`POST /api/portfolio/trade` does and for the same reason: the turn can execute
trades, and with a dead feed every price is frozen at its last value. A chat
that kept filling against them would be a way around the refusal the trade bar
gives.

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

At most **10 trades and 10 watchlist changes** are honoured per reply. The model
is told both limits in the system prompt, and anything beyond them is reported
back to the user rather than silently dropped — the chat path executes without
a confirmation dialog, so nothing else stands between a looping model and the
ledger.

**Cerebras rejects `pattern` in a structured-output schema**, answering
`Invalid fields for schema with types ['string']`. OpenRouter's
`provider.order` is a *preference*, not a pin, so it silently served those
requests from another host instead: every call succeeded and nothing looked
wrong, but no run was on the provider this section requires. The schema sent
over the wire therefore has that keyword stripped, while the Pydantic model
keeps it and still validates every action — the provider was never the
authority on what a ticker is. Fallbacks stay enabled so a Cerebras outage
costs latency rather than the assistant, and the backend logs a warning
whenever something other than Cerebras served a request.

**The reply is parsed action by action, not as one document.** A single
malformed trade must not discard the model's message and its nine good actions
along with it; each bad item becomes a reported rejection instead. This is
salvage, never trust — every surviving action is validated again by
`app.portfolio` and `app.watchlist`.

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

- **Watchlist panel** — grid/table of watched tickers with: ticker symbol, current price (flashing green/red on change), daily change %, and a sparkline mini-chart (accumulated from SSE since page load). It also carries the manual add/remove control §2 promises: a symbol field in the header and a remove button per row, both reporting the backend's own refusal — 409 duplicate, 400 list full, 503 no feed. Added at Checkpoint 6, beside the trade bar, because no earlier checkpoint's scope claimed it
- **Main chart area** — larger chart for the currently selected ticker, with at minimum price over time. Clicking a ticker in the watchlist selects it here.
- **Portfolio heatmap** — treemap visualization where each rectangle is a position, sized by portfolio weight, colored by P&L (green = profit, red = loss)
- **P&L chart** — line chart showing total portfolio value over time, using data from `portfolio_snapshots`
- **Positions table** — tabular view of all positions: ticker, quantity, avg cost, current price, unrealized P&L, % change
- **Trade bar** — simple input area: ticker field, quantity field, buy button, sell button. Market orders, instant fill.
- **AI chat panel** — docked/collapsible sidebar. Message input, scrolling conversation history, loading indicator while waiting for LLM response. Trade executions and watchlist changes shown inline as confirmations.
- **Header** — portfolio total value (updating live), connection status indicator, cash balance
- **Feed panel** — frames received, time of the last update, tickers priced, and the notable moves
  the SSE `event: shock` frames carry. Added at Checkpoint 5: it is what distinguishes a quiet
  market from a dead connection, and until then nothing consumed those frames

**One `EventSource` per page.** `usePriceStream` opens a connection per call, so `TerminalProvider`
is its only caller and every panel reads prices through `useMarket()`. A component that opens its
own stream costs the backend a second copy of every frame, and nothing looks wrong until the count
is high.

### Technical Notes

- Use `EventSource` for SSE connection to `/api/stream/prices`
- **Charts are hand-rolled SVG, not a charting library.** This section preferred a
  canvas library "for performance", and the measurement is what overruled it: what
  arrives at 2 Hz is one new point on one polyline, so a render is a coordinate
  string and a path substitution, not a scene graph. A library would have added a
  dependency, an imperative instance to dispose of on unmount — this checkpoint's
  stated review focus — and a jsdom shim, to replace a `<path>` React already
  reconciles. `components/LineChart` is the one plot both time series use
  - The plot is drawn in a fixed 0–100 box and stretched with
    `preserveAspectRatio="none"`, so it fills a panel of any shape with nothing
    to measure and no `ResizeObserver`. **It must be given both `h-full` and
    `w-full`**: an `svg` is a replaced element with an intrinsic aspect ratio
    taken from its `viewBox`, so a height alone makes the browser compute a
    square. Strokes carry `vector-effect="non-scaling-stroke"`, and every label
    is HTML positioned over the box rather than `<text>` inside it, so the
    distortion reaches neither line widths nor type
  - The y axis spans the series' own range, never zero. A portfolio moving
    between 10,000 and 10,050 is a flat line on a zero-based axis, and the shape
    of that move is the whole point of the panel
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

Every checkpoint passes through four gates, **in order**. No gate may be skipped, and no checkpoint
is marked ✅ until all four have passed. Work does not begin on checkpoint N+1 while N sits at an
unpassed gate.

**The order is the point, and it was learned the expensive way.** Checkpoints 1–3 ran review
*after* full hardening, so every review finding and every simplification invalidated the mutation
set, the fixtures and the live verification, and all of it was redone. Checkpoint 3 ran its
mutation suite three times and its live smoke twice for that reason alone. Review is now cheap and
early; the brittle, expensive verification runs **once**, against code nobody is going to change
again.

#### Gate 1 — Build

Fast, iterated freely. Nothing here is expensive, so run it as often as you like.

1. Unit tests covering the new code exist and pass (`uv run --extra dev pytest` for backend,
   `npm test` for frontend)
2. The **full** suite is green, not just the new tests — nothing from an earlier checkpoint regressed
3. Linting is clean (`ruff check app/ tests/`; `npm run lint` and `tsc --noEmit` for frontend)
4. **Commit.** Green is a checkpoint you can return to; see the hard rules below

Do **not** run mutation testing, the live smoke, or the three-consecutive-runs check here. They
belong at Gate 3, after the code has stopped moving.

#### Gate 2 — Review

Straight after Gate 1, while changes are still cheap to make.

1. Run `/code-review high` over the branch diff — **one agent.** Every **CONFIRMED** correctness
   finding is fixed; every **PLAUSIBLE** one is either fixed or answered in writing on the PR —
   silently ignoring one is not an outcome
2. Run `/security-review` on any checkpoint handling untrusted input, money movement, or secrets.
   Required for Checkpoints 4 and 8; optional elsewhere
3. **Structure pass.** Ask one question: *can the next checkpoint call this without going through
   HTTP?* Spawn a single agent for it only when the checkpoint adds new modules or a new layer;
   otherwise do it inline. This is the pass that earns its keep — it is what found that Checkpoint
   3's watchlist rules lived only inside FastAPI handlers, leaving Checkpoint 4 nothing to call
4. Re-run **Gate 1** (seconds), not the whole hardening suite
5. Commit the fixes

`/simplify`'s four-agent fan-out is **retired.** Run at Checkpoint 3 it cost roughly 350k subagent
tokens and returned the same finding three times over — the duplicated history limit, the
duplicated "is held" predicate and the phantom `price_cache` argument each came back from multiple
angles. One structure agent plus an inline read of the diff found everything that mattered at a
fraction of the cost.

#### Gate 3 — Verify

Runs **once**, on code that is not going to change again. If a Gate 3 failure forces a code change,
return to Gate 2 rather than patching forward.

1. Every exit criterion listed for the checkpoint passes, verified by **actually running it** — not
   by reading the code and concluding it should work. Keep the commands in a re-runnable smoke
   script under `test/`, not hand-assembled at the terminal, so the second run costs nothing
2. Full suite green three consecutive times, plus once under coverage — the coverage run is slower
   and has already exposed one timing-dependent test that passed bare
3. Coverage has not dropped below the previous checkpoint's figure; record it in the status table
4. **Mutation testing, scoped to the invariants this checkpoint owns** — the money rules, the
   atomicity, the domain-specific ones. Ten to fifteen well-chosen mutations, not forty: mutating
   request schemas and route wiring mostly re-proves what ordinary tests already assert
5. Commit

Tests must be capable of failing for the right reason. A test that passes against a deliberately
broken implementation is not coverage — see Checkpoint 1, where thirteen `MagicMock`-based tests
passed against a client that could never populate the cache, and Checkpoints 2 and 3, where
mutation testing exposed vacuous tests every single time. This is why the step survives being
scoped down; it must not be skipped.

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

#### Gate 4 — Record

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
4. Branch name `checkpoint-N-<slug>`; open the PR to `main` **with `--repo` and `--base` given
   explicitly** — this repo is a fork, and a bare `gh pr create` targets the upstream parent.
   `.github/workflows/claude-code-review.yml` reviews on open and on every push; unresolved
   comments block merge

Items 1–3 land in the same commit as the code, not a follow-up commit. A green checkpoint with a
stale `PLAN.md` has not passed Gate 4.

If an exit criterion or a gate cannot be met, do not mark the checkpoint done and move on. Set the
row to ⛔, write a log entry describing how far it got and what blocked it, and raise it.

#### Hard rules — each one is a mistake already made

- **Never run `git checkout --`, `git restore` or `git reset --hard` against a dirty tree.** In
  Checkpoint 3 that command was used to clear a stray file and silently discarded every uncommitted
  review fix in `app/` — eight files, rewritten from scratch to recover. Commit first, always; the
  Gate 1 and Gate 2 commits exist so there is something to fall back to
- **Run mutation testing in a `git worktree`, never the working tree.** The harness restores files
  in a `finally`, which a `SIGKILL` skips — that is how a mutant was left behind and provoked the
  destructive command above. A throwaway worktree makes the whole class of accident impossible
- **Give every mutation subprocess a timeout.** Removing `snapshot_task.cancel()` does not fail the
  suite, it hangs it; a timeout is a detection, not an error
- **Use absolute paths in shell commands.** The working directory resets between calls, and
  `cd backend` from an unknown cwd failed repeatedly in Checkpoint 3
- **Prefer inline work to spawning agents.** A cold agent re-derives context that is already loaded.
  Spawn for genuinely independent judgement — the code review, the structure pass — not for work
  that can be done directly

### Status

| # | Checkpoint | Depends on | G1 Build | G2 Review | G3 Verify | G4 Record | Coverage | Status |
|---|---|---|---|---|---|---|---|---|
| 1 | Market data hardening | — | ✅ | ✅ | ✅ | ✅ | 100% | ✅ Complete (PR #4) |
| 2 | Backend skeleton + database | 1 | ✅ | ✅ | ✅ | ✅ | 100% | ✅ Complete (PR #5) |
| 3 | Portfolio & watchlist API | 2 | ✅ | ✅ | ✅ | ✅ | 100% | ✅ Complete (PR #6) |
| 4 | LLM chat integration | 3 | ✅ | ✅ | ✅ | ✅ | 100% | ✅ Complete (PR #7) |
| 5 | Frontend scaffold + live prices | 2 | ✅ | ✅ | ✅ | ✅ | 100% | ✅ Complete (PR #8) |
| 6 | Charts, portfolio visualisation, trade bar | 3, 5 | ✅ | ✅ | ✅ | ✅ | 100% | ✅ Complete (PR #10) |
| 7 | Chat panel | 4, 6 | ⬜ | ⬜ | ⬜ | ⬜ | — | ⬜ Not started |
| 8 | Docker packaging + start/stop scripts | 7 | ⬜ | ⬜ | ⬜ | ⬜ | — | ⬜ Not started |
| 9 | End-to-end test suite | 8 | ⬜ | ⬜ | ⬜ | ⬜ | — | ⬜ Not started |
| 10 | Polish, docs, and release readiness | 9 | ⬜ | ⬜ | ⬜ | ⬜ | — | ⬜ Not started |

Legend: ⬜ not started · 🔨 in progress · ✅ complete · ⛔ blocked

A checkpoint's Status may only read ✅ when G1, G2, G3 and G4 all read ✅. Coverage records the backend
figure at Gate 3, and must not fall from one checkpoint to the next.

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
`/api/portfolio/history`, positions table, the trade bar, and the manual watchlist add/remove
control — carried forward from Checkpoint 5, which found that §2 promises it and no checkpoint's
scope had claimed it. It belongs beside the trade bar.

**Exit criteria:**

- Clicking a watchlist row selects that ticker in the main chart
- A buy from the trade bar updates cash, positions table, heatmap, and header total with no reload
- The heatmap sizes by portfolio weight and colours by P&L sign, and survives an empty portfolio
- The P&L chart renders the snapshot series and extends as new snapshots arrive
- A rejected trade surfaces a visible error instead of failing silently
- Adding a ticker from the watchlist panel starts it streaming; removing one drops the row without
  disturbing a position held in it

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

Append-only. One entry per checkpoint, written at Gate 4, in the same commit as the code. Newest
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

Entries 1–4 were condensed on 2026-08-12. No fact was dropped — every exit criterion, review
finding, divergence and carried-forward item survives; the narrative retellings were cut.

#### Checkpoint 1 — Market data hardening

- **Closed:** 2026-08-09 · branch `checkpoint-1-market-data-hardening` · PR #4 · all gates passed;
  the review workflow raised nothing on any push
- **Built:** all 17 changes from `MARKET_DATA_DESIGN.md` §17 across `backend/app/market/` —
  `models.py` (`normalize_ticker`, `previous_close`/`day_change*`, `MarketEvent`) · `events.py`
  (**new** `EventLog`, bounded ring buffer with per-client cursors) · `cache.py` (normalisation at
  every entry, sticky `previous_close`) · `interface.py` (`PermanentMarketDataError`, plus
  `market_status` and `on_permanent_failure` on the ABC) · `simulator.py` (injected RNGs, log-space
  shocks, `event_probability` 2e-5, session-open baseline, `drain_events()`) · `massive_client.py`
  (the `extract_price`/`extract_timestamp`/`extract_previous_close` ladder,
  permanent-vs-transient classification, 5 s timeouts) · `factory.py` (`start_market_data()`,
  fail-fast-then-fallback; `MASSIVE_POLL_INTERVAL` / `SIM_UPDATE_INTERVAL` /
  `SIM_EVENT_PROBABILITY`) · `stream.py` (heartbeats, `event: shock`, `event: status`)
- **Exit criteria:** all met
  - Fixture test fails if the ladder is reverted — mutation-verified: restoring
    `snap.last_trade.timestamp / 1000.0` fails 16 tests, which the old `MagicMock` suite passed
  - `test_source_contract.py` — 7 tests × 2 sources
  - Invalid key yields a running simulator, plus the authenticated-but-no-prices case
  - Realised volatility over a full 46,800-tick session, shocks off: every ticker **1.00–1.07×** its
    configured sigma (AAPL 0.230/0.22, TSLA 0.501/0.50, JPM 0.193/0.18) — target was within 2×
  - **228 passed, 100%** on `app/market/` (target ≥ 85%)
- **Tests:** 73 → **228**. Three consecutive green runs, lint clean. Coverage 84% → **100%**.
  Runtime 6.5 s → **2.0 s**, zero network. Two of my own tests were wrong and were rewritten — one
  derived the shock magnitude from its own result, one hung instead of failing; mutation testing
  caught both
- **Review:** `/code-review high` — **8 findings, 7 fixed, 1 deferred**
  1. `is_permanent_failure` matched the raw body: real Polygon bodies missed, a hex `request_id`
     could false-positive → parses `status`/`message`/`error` and matches real wording
  2. `on_permanent_failure` accepted but never wired → **deferred to CP2**, where the lifespan
     exists; carried forward below
  3. Unguarded `status_provider()` raised `AttributeError` under the simulator, aborting the SSE
     response and looping `EventSource` → guarded, in code and in the documented snippet
  4. `_poll_loop` caught only `PermanentMarketDataError`; anything else killed the poller for the
     process lifetime → catch-all plus per-snapshot guarding
  5. `len(price_cache) == 0` passes vacuously on a shared cache → checks the requested tickers and
     names the missing ones
  6. Fallback simulator ignored the `SIM_*` env vars → one `_create_simulator` helper
  7. `MASSIVE_POLL_INTERVAL=` crashed startup → `_env_float`
  8. `update_interval` not propagated to `dt`, silently mis-scaling volatility → derived from the
     tick rate

  `/security-review` not run (optional here). `/simplify` found one thing that mattered — **the
  `[massive]` contract tests were making real HTTPS calls**, since `start()` overwrites `_client`;
  patched at class level, 6.5 s → 2.0 s — plus: `market_status`/`on_permanent_failure` onto the ABC,
  `EventLog.__len__` deleted, one owner per tuning default, the ABC's over-promising `start()`
  docstring corrected
- **Diverged from plan:** one. §16.4's `httpx.ASGITransport` SSE tests cannot work — the generator
  is infinite and ASGITransport never delivers `http.disconnect`, so closing the response hangs.
  `test_stream.py` drives `_generate_events` with a stub request that disconnects after N ticks.
  §16.4 corrected
- **Closing pass (same branch):** last two uncovered lines tested and mutation-verified — the
  `_refresh_market_status` guard needed an assertion on *silence*, since the catch-all below
  swallows the `AttributeError` a "does not raise" test allows. `market_data_demo.py` now consumes
  `EventLog` instead of a dead `abs(change_percent) > 1.0` heuristic. `httpx` kept: `TestClient`
  needs it
- **Carried forward:**
  - `on_permanent_failure` is public and tested but nothing assigns it — **CP2's lifespan must**
    (review finding #2, deferred not resolved)
  - `status_provider` can now be `lambda: source.market_status`
  - `EventLog` reaches simulator → factory → stream but no *server* consumer builds one; CP2 must
    create it in the lifespan and pass it to both
  - 100% on `app/market/` is a floor, not a target reached: two of the newest tests only became
    meaningful once mutation testing showed the obvious version passing against broken code

#### Checkpoint 2 — Backend skeleton + database

- **Closed:** 2026-08-09 · branch `checkpoint-2-backend-skeleton-db` · PR #5 · all gates passed
- **Built:** `app/main.py` (`create_app()` with its own `PriceCache` + `EventLog`; lifespan loads
  tracked tickers, starts the feed, wires failover, stops on shutdown; static mount tolerating an
  absent build) · `app/db/schema.sql` (the six §7 tables + three `(user_id, time)` indexes) ·
  `app/db/database.py` (`connect()`, `transaction()`, race-safe lazy init, seeding,
  `load_tracked_tickers()`) · `app/api/health.py` (probes the database for real) · `app/paths.py`
  (**new** — one owner for `BACKEND_DIR` / `REPO_ROOT` / `is_source_checkout()`) · `app/market/`:
  `DEFAULT_TICKERS`, public `create_simulator_source()`, `MassiveDataSource._teardown()`
- **Exit criteria:** all four met against a live server, twice — at Gate 1 and after the review
  - `GET /api/health` → **200**, reporting `SimulatorDataSource`, 10 tickers
  - Deleting `db/finally.db` and issuing any request recreates it, **twice in a row**: 6 tables,
    profile at $10,000, 10 watchlist rows; also covered at both layers in the suite
  - SSE: 4 price frames in 2 s; `: keep-alive` observed with `SIM_UPDATE_INTERVAL=600`, since the
    heartbeat needs a genuinely idle cache
  - Database, stream and `/api/health` agree on the same ten seeded tickers
- **Tests:** 228 → **295**. Three consecutive green runs. Coverage **100%** on `app/`, holding CP1's
  floor
- **Review:** `/code-review high` — **6 findings, all fixed.** The first two were HIGH and
  compounding
  1. Lazy init was not atomic: `executescript()` implicitly COMMITs a pending transaction, so
     `BEGIN IMMEDIATE` committed before the schema ran and `rollback()` rolled back nothing. A
     failed seed left six empty tables whose *presence* was the "initialised" test → the app ran
     forever with no cash balance. Fixed twice over: the schema is split with
     `sqlite3.complete_statement` and run inside the transaction, and `_is_initialized` requires the
     seeded profile row, so such a database repairs itself
  2. The test for #1 was vacuous — `monkeypatch.undo()` also reverted the autouse `DB_PATH` fixture,
     so it asserted against the developer's real database → `MonkeyPatch.context()`, and it now
     asserts `DB_PATH` survived
  3. The health probe discarded its result, so an unseeded database reported "ok" → fixed at the
     root in #1; the shallow duplicate deleted during `/simplify`
  4. `load_tracked_tickers` normalised *after* the `UNION`, so `aapl` and `AAPL` both survived —
     one ticker priced and sent twice per Massive request → dedupe after normalising
  5. A failure inside the failover handler escaped into the dying source's task, surfacing only as
     "Task exception was never retrieved" while `/api/health` reported the dead source as live →
     caught, logged, slot cleared, callback assigned to the replacement
  6. Shutdown read `app.state.market_source` once, so a failover mid-shutdown left a simulator
     ticking on → a `shutting_down` flag that closes the window rather than draining it

  `/security-review` not run (optional here). Every fix mutation-verified. `/simplify`: the "never
  stop the source that just failed" invariant moved into `MassiveDataSource`, which tears down
  before awaiting the callback; `app/paths.py` created; the health endpoint's unreachable branch
  deleted; test duplication consolidated into `conftest.py`; `connect()` measured at ~500 µs, not
  the "microseconds" its docstring claimed — fine at human cadence, never on the 500 ms tick. Two
  things found while verifying were worse than any finding:
  - **`tests/db` flaked "database is locked" ~1 run in 6.** Real: `PRAGMA journal_mode=WAL` takes a
    brief exclusive lock, SQLite returns `SQLITE_BUSY` for it without consulting the busy handler,
    and it ran before `busy_timeout` was set — so several first requests together could 500. Fixed
    by setting `busy_timeout` first and tolerating a contended WAL switch: **12/12 clean, the old
    ordering fails 2/12**, with a deterministic test because a 1-in-6 reproduction is not a gate
  - **A new test of mine was vacuous twice over** — it awaited `_poll_loop()` directly so `stop()`
    had nothing to cancel, and once fixed still passed against the broken source because `stop()`
    swallows a self-cancel. It now asserts `task.cancelling() == 0`
- **Diverged from plan:** three, all reflected in the spec — `load_tracked_tickers` is synchronous
  and `on_permanent_failure` assigned plainly (§13.1 corrected); `DB_PATH`, `STATIC_DIR`,
  `LOG_LEVEL` added to §5 with working defaults; `schema.sql` adds three `(user_id, <time>)` indexes
- **Carried forward:**
  - **`app/api/deps.py` is CP3's first task** — six new handlers would each repeat "what if
    `market_source` is `None`?"; `get_market_source` should 503 once, with `/api/health` the
    deliberate exception
  - Watchlist handlers must be `async def` with the SQLite call in `run_in_threadpool`: a `def`
    handler cannot `await source.add_ticker()`, and mutating the watchlist without telling the
    source leaves a ticker permanently unpriced
  - `transaction()` is tested but has no production caller; CP3's trade path is its first
  - The 30-second `portfolio_snapshots` task is **not built** — CP3 scope, in the lifespan
  - `app.state.shutting_down` is read by the failover handler; anything else installing a source
    must respect it
  - Coverage 100% is a floor: three of this checkpoint's tests passed against deliberately broken
    code before mutation testing exposed them

#### Checkpoint 3 — Portfolio & watchlist API

- **Closed:** 2026-08-10 · branch `checkpoint-3-portfolio-watchlist-api` · PR #6 · all gates passed
- **Built:** `app/api/deps.py` (`get_price_cache` / `get_market_source`, the latter 503-ing once) ·
  `app/db/repository.py` (**new** — row access for all six tables as connection-taking functions, so
  a trade composes four inside one `transaction()`; `Position`/`Trade`/`Snapshot`/`WatchlistEntry`;
  `apply_position()` owns "quantity zero means no row") · `read_transaction()` (`BEGIN DEFERRED`) ·
  `app/portfolio.py` (**new** — the single implementation of valuation and trade execution:
  `execute_trade`, `get_portfolio`, `get_history`, `record_snapshot`, `TradeError`, and the
  `PortfolioView`/`PositionView`/`TradeResult` shapes) · `app/watchlist.py` (**new** — `add`,
  `remove`, `reconcile`, `WatchlistError`; `reconcile()` is the sole enforcer of
  "tracked = watchlist ∪ positions") · `app/api/portfolio.py`, `app/api/watchlist.py`,
  `app/api/schemas.py` (the six §8 endpoints, now thin) · the 30-second snapshot task in the
  lifespan, cancelled *and awaited* at shutdown
- **Exit criteria:** all five met; the last four over HTTP against a live `uvicorn`
  - The seven money cases are in `tests/test_portfolio.py`, each mutation-verified
  - PYPL and SQ added via `POST /api/watchlist` appear in the next `data:` frame at a real price
  - `DELETE /api/watchlist/AAPL` while holding 5 → `still_tracked: true`, position and live mark
    kept; after restart `/api/health` reported 12 tracked for an 11-ticker watchlist
  - Live history: four points 30 s apart, then a fifth at the trade's timestamp
  - Every documented shape and code exercised live: 200/201/400/404/409/422/503
- **Tests:** 295 → **443**. Three consecutive green runs, three times over (Gate 1, post-review,
  post-simplify). Coverage **100%**; lint clean; 4.2 s, no network. **36 mutations, all killed** —
  one per money rule, per review fix, and per watchlist rule. Two survived the first pass, both real
  test gaps:
  - The exact-balance buy test could not distinguish `>` from `>=`, which showed **the cash
    tolerance was dead weight**: cash and cost are both `round(…, 2)`, so they compare exactly. It
    was deleted; the asymmetry with the load-bearing `QUANTITY_TOLERANCE` is documented where it was
  - "The trade and its snapshot read the same prices" passed against a version that re-read the
    cache, since a static cache cannot tell one read from two → `DriftingPriceCache`

  Two side-findings: removing `snapshot_task.cancel()` **hangs** the suite rather than failing it
  (a timeout is the detection), and a shutdown assertion read its baseline inside the lifespan —
  green bare, red under coverage
- **Review:** `/code-review high` — **7 findings, all fixed**
  1. Gate 3 not performed; `backend/CLAUDE.md` still said CP3 *should* add `deps.py` → this entry,
     the status row, §7, §8 and a rewritten wiring section
  2. `monkeypatch.setattr("app.main.SNAPSHOT_INTERVAL_SECONDS", …)` did nothing — a default binds at
     def time — so the lifespan test ran at the real 30 s → `interval` is `None`-defaulted and
     resolved in the body
  3. `get_portfolio()` read cash and positions in autocommit, so a trade committing between them
     yields a total that never existed → `read_transaction()`, tested by running a real trade from
     inside the cash read
  4. The DELETE held-check and `remove_ticker()` were not atomic, stranding a position with no price
     source → `_unsubscribe()` re-reads after eviction and re-subscribes
  5. Snapshots persisted a total that silently omitted unpriced positions — a permanent phantom
     drawdown → skip and log; §7 corrected
  6. `POST /api/portfolio/trade` took no `get_market_source` dependency, so it kept filling against
     frozen prices after a failed failover → now depends on the source it does not use
  7. Rollback asymmetry: POST compensated, DELETE did not → both directions now do

  `/simplify` found one structural problem: **the watchlist had no callable path** — its rules lived
  inside handlers that signal by raising `HTTPException`, so CP4 would have had to re-implement them
  or catch a `409` FastAPI would then apply to `POST /api/chat`. Hence `app/watchlist.py`, handlers
  reduced to an error-code map. **`reconcile()` replaced three pieces of incremental bookkeeping**
  and closes the delete race by re-reading after its removals. Also: one owner each for the history
  limit, `round(price * quantity, 2)` and display precisions; `TradeResult.position` →
  `result.position()`; `get_history`'s unused argument deleted; `delete_position` made private; test
  helpers moved to `conftest.py`. Skipped, with reasons: Pydantic response models (a second
  definition of every shape — revisit at CP5), a staleness bound on quotes (threshold must come from
  the poll interval — carried forward), deleting `list_trades` (kept as dated CP6/CP7 scaffolding).

  `/security-review` **run and required. No HIGH or MEDIUM findings.** Cleared: every new query
  parameterised; `TradeRequest` has no `price` field and forbids extras; `user_id` unreachable from
  any request; the `{ticker}` path parameter is pattern-bound and reaches only SQL parameters and
  dict keys; the ticker forbids `/`, `:` and `%` and reaches Massive as a query argument;
  `BEGIN IMMEDIATE` stops two concurrent buys spending the same balance. Unbounded watchlist growth
  is real but out of that review's scope — carried forward
- **Diverged from plan:** three, all now in the spec — §7's "immediately after each trade" is
  qualified by review #5; §8 gained a status-code column and the 400-versus-422 rule;
  `POST /api/portfolio/trade` requires a running market source. No new env vars
- **Resolved from CP2:** `deps.py` built; handler colours settled (`async def` for watchlist, plain
  `def` for portfolio) and `backend/CLAUDE.md` rewritten; `transaction()` has its first production
  caller; the snapshot task is built; `shutting_down` untouched, and the snapshot task is cancelled
  *and awaited* before the source stops
- **Carried forward:**
  - **The watchlist has no size limit** and every entry joins every Massive poll — **CP4 should add
    a cap**, since CP4 hands this endpoint to an LLM that can call it in a loop
  - **A ticker stays subscribed after its position closes.** Deliberate — the chart going flat the
    instant you sell would be worse — but the tracked set only grows within a session
  - `list_trades()` is written and tested with no production caller — for CP6 or CP7
  - The `_unsubscribe` race is narrowed, not eliminated: closing it needs the subscription and the
    row under one lock, which the async/sync split prevents
  - `read_transaction()` has one caller; any future multi-statement read must use it
  - **A source that is alive but stalled is still undetected** — `require_live_market` catches a
    feed that is *gone*, not a wedged poller. The fix is a staleness bound on `PriceUpdate.timestamp`
    inside `_require_price`; the threshold must come from the poll interval (0.5 s simulator against
    15 s Massive) and too tight a bound silently blocks valid trades
  - `reconcile()` costs two `load_tracked_tickers()` reads per mutation (~1 ms). Deliberate: the
    second read is what turns a buy-during-removal into an add
  - Coverage 100% remains a floor — two of this checkpoint's tests passed against broken code until
    mutation testing exposed them, the third checkpoint running where that is true

#### Checkpoint 4 — LLM chat integration

- **Closed:** 2026-08-10 · branch `checkpoint-4-llm-chat` · PR #7 · all four gates passed. First
  checkpoint under the reworked gate order, and it paid for itself: review landed before the
  expensive verification, so ten fixes cost one mutation run rather than three
- **Built:** `app/llm/` (**new package**) — `schema.py` (`AssistantReply`, `LLMTrade`,
  `LLMWatchlistChange`, `parse_reply` validating **action by action**, `wire_schema()` stripping
  what Cerebras rejects) · `prompt.py` (`SYSTEM_PROMPT`, `render_context`, `build_messages`; rules
  and account data are `system` messages, anything a user or the model wrote is `user`/`assistant`)
  · `client.py` (LiteLLM → OpenRouter → Cerebras, `LLMUnavailableError`, `is_mock_enabled`,
  `_log_provider`) · `mock.py` (returns **raw JSON**, so mock runs exercise the real parser). Plus
  `app/chat.py` (**new** — `handle_message`, `get_transcript`, `ChatReply`, `ActionResult`;
  orchestration and persistence, executing nothing itself) · `app/api/chat.py` + `ChatRequest` ·
  `app/config.py` (**new** — `load_env()`, delivering §5's promise) · repository additions
  (`ChatMessage`, `insert_chat_message`, `list_chat_messages`, `count_watchlist`) ·
  `MAX_WATCHLIST_SIZE` (50) and `WatchlistFullError` · `TICKER_PATTERN` moved to
  `app/market/models.py`
- **Exit criteria:** all five met, the middle three over HTTP via `test/smoke.sh`
  - `{"message":"buy 2 MSFT"}` under `LLM_MOCK=true` → `ok: true`, balance moved; also asserted in
    `TestMockMode`, which runs the production `complete()`
  - Every malformed shape (`""`, `not json`, `[1,2,3]`, no `message`, `message: null`) → 200 with
    `MALFORMED_REPLY_MESSAGE`
  - `buy 100000 AAPL` → 200, `ok: false`, "Insufficient cash…", cash unmoved
  - Both turns persist in one transaction with `actions` JSON on the assistant row, and the next
    call's prompt carries them back
  - One live call succeeded — **and found the checkpoint's worst defect** (below)
- **Tests:** 443 → **692**. Coverage **100%**; lint clean; 8.1 s, no network in the suite.
  **37 mutations, all killed** — 19 inherited plus 18 for this checkpoint's invariants: the model
  naming its own fill price, an infinite order size, the per-reply cap, a refused action being
  swallowed, the action ordering, ticker normalisation, persisting a turn the provider never
  answered, the context block arriving as a `user` message, `LLM_MOCK` falling through to a live
  call, provider error text reaching the user, and both halves of the watchlist cap. One survived
  and was genuinely vacuous — the **third checkpoint running** where that is true: "the compensating
  restore is not refused by the cap" never reached the check, because `remove()` deletes before it
  restores. Something else now takes the freed slot first
- **Review:** `/code-review high` — **11 findings, 10 fixed, 1 was Gate 4 work**
  1. Watchlist changes ran before trades, so a `remove` evicted the price a `buy` in the same reply
     needed → adds, then trades, then removes
  2. Un-normalised tickers reached `ActionResult` and `chat_messages.actions`, so one request could
     report two spellings of one symbol — the field CP7 matches rows by → normalised once per action
  3. The mock read "buy 3 shares" as a trade in `SHARES` → the stop-list applies to trades too
  4. "watch for a dip" added `FOR`, and the simulator invents a price for any symbol, so the junk
     row really streamed → same change, prepositions added
  5. History replay dropped `actions`, so a refused buy left "I've bought 10 AAPL" in the transcript
     and the model read its own claim back as fact → an assistant turn replays with what actually
     executed appended
  6. `remove()`'s compensating restore could itself raise `WatchlistFullError`, replacing the real
     error and leaving the row deleted → the restore bypasses the cap
  7. `_load_context` reads the portfolio outside its `read_transaction` — **comment corrected,
     behaviour left**: folding it in means exporting private valuation so advisory prose can be
     atomic; the cost is two rendered prices one tick apart, and nothing is computed from the pair
  8. `_finish` persisted after the trades committed, so a failed write returned 500 for a request
     that had already moved cash — where the obvious retry buys twice → logged and swallowed, the
     reply returned regardless
  9. The over-cap branch appended one rejection per item, not one for the remainder → fixed in
     favour of the comment, so twenty identical sentences cannot bury the turn's other failures
  10. `load_env()` ran at import, which `monkeypatch` cannot undo, leaking a developer's `.env` into
      the suite → `conftest.py` clears every application variable, listed exhaustively
  11. `PLAN.md` and `backend/CLAUDE.md` untouched → this entry and the docs

  `/security-review` **run and required. No HIGH or MEDIUM findings.** Cleared: `LLMTrade` has no
  `price` field and forbids extras; `user_id` unreachable, since `ChatRequest` and both action
  models forbid extras; every new query parameterised including the `limit`; `TICKER_PATTERN` still
  forbids `/`, `:` and `%`; the provider's error text — which quotes the failing request back — is
  confined to the server log, asserted by planting a key-shaped string in the exception;
  `json.loads` is the only deserialisation. Prompt injection is out of that review's scope; the
  substantive mitigation is that the model moves money only by returning an action that is then
  re-validated.

  The structure pass was **inline** — the review had just covered the same diff. Its one finding:
  `app/llm/schema.py` imported `TICKER_PATTERN` from `app.api.schemas`, an upward dependency from
  the LLM contract to the transport layer; moved to `app/market/models.py`. Nothing outside
  `app/api/` now imports `app.api`, and `handle_message` / `get_transcript` take no `Request` and
  raise no `HTTPException`, so CP7 can drive a whole turn without HTTP
- **The live call was worth the whole checkpoint.** 683 tests, the smoke script and a real
  end-to-end fill all passed while every request was served by **CoreWeave, not Cerebras**:
  `provider.order` is a preference, not a pin, and Cerebras was refusing outright with
  `Invalid fields for schema with types ['string']: {'pattern'}` — `Field(pattern=...)` puts
  `pattern` into the generated JSON schema. Nothing failed, so nothing was visible; it surfaced only
  because the live check printed the provider. Fixed by stripping the keyword from the wire schema:
  `provider='Cerebras'`, **0.43 s** per turn against several seconds, and the model started reading
  the cash balance correctly. Per the gate rules this Gate 3 failure returned to Gate 2.
  `test_the_wire_schema_avoids_what_cerebras_rejects` and `_log_provider` make the next drift loud
- **Diverged from plan:** four, all now in the spec — `GET /api/chat/history` is new (CP7 needs it,
  and the alternative was undated scaffolding); `POST /api/chat` requires a running market source;
  the wire schema is not `AssistantReply` verbatim; the watchlist is capped at 50. No new env vars —
  what changed is that `.env` is now actually read
- **Resolved from CP3:** the watchlist cap is built, enforced inside the insert's transaction so a
  duplicate on a full list still reports as a duplicate
- **Carried forward:**
  - **One unreproducible test failure.** A single full-suite run failed once (`1 failed, 691
    passed`) and could not be reproduced in **63 further runs** — bare, under coverage, under
    tenfold CPU load, and repeating the exact smoke-then-suite sequence. Its identity is lost
    because the run was piped through `tail -1`. CP4 added no timing-dependent test; every fixed
    sleep is inherited from CP1–CP3 (`test_main.py`, `test_simulator_source.py`, `test_massive.py`).
    ~~**CP9 owns flaky-test sources and must resolve this**~~ — **resolved** in Checkpoint 5's
    follow-up pass: it is `test_keeps_appending_every_interval`, and it was I/O latency
  - ~~**A stalled-but-alive source is still undetected**~~ — **resolved** in Checkpoint 5's
    follow-up pass. The bound is on receipt time, not the venue timestamp, and comes from the
    source's own cadence
  - **A ticker stays subscribed after its position closes**, unchanged; the chat can reach this too
  - **The mock's stop-list is a heuristic, not a parser** — "buy 3 widgets" still trades `WIDGETS`.
    Bounded to `LLM_MOCK=true`, so it costs CP9 a carefully worded fixture
  - `list_trades()` is still uncalled — CP6/CP7 surfaces it or CP10 deletes it, one checkpoint older
    than the note that said so
  - `read_transaction()` now has two callers; the `_unsubscribe` race is unchanged
  - **`app/llm/prompt.py` renders context as prose and nothing tests what the model *does* with
    it** — obedience is established only by a live call, and there is exactly one
  - Coverage 100% is still a floor. One of this checkpoint's tests passed against broken code until
    mutation testing exposed it — four checkpoints, four times

#### Checkpoint 5 — Frontend scaffold + live prices

- **Closed:** 2026-08-12 · branch `checkpoint-5-frontend-scaffold` · PR #8 · all four gates passed
- **Built:** `frontend/` — Next.js 16 + TypeScript, `output: 'export'`, Tailwind v4
  - `next.config.ts` — static export, `trailingSlash` so Starlette's `StaticFiles(html=True)`
    resolves a directory to its `index.html`
  - `app/globals.css` — the §2 palette and the three brand colours as a Tailwind v4 `@theme`
    block, plus the `flash-up` / `flash-down` keyframes and a `prefers-reduced-motion` opt-out
  - `hooks/usePriceStream.ts` — one `EventSource`, four connection states, sparklines accumulated
    from page load in a 120-point window, and the `event: shock` frames
  - `hooks/usePriceFlash.ts` — direction plus a sequence used as the element's React `key`
  - `hooks/useApiResource.ts` — fetch-once with a **derived** `loading`, so a reload or a changed
    path is right without a flag to remember, and the effect holds no synchronous `setState`
  - `state/TerminalProvider.tsx` — **the structure pass's finding.** The single stream, the
    account, and `refresh()`; `useMarket()` / `useAccount()`
  - `components/` — `WatchlistPanel` (+ `TickerRow`), `Sparkline`, `Header`, `ConnectionDot`,
    `FeedPanel`
  - `lib/` — `api.ts` (base URL, `getJson`, `ENDPOINTS`), `types.ts`, `format.ts`, `valuation.ts`
  - `test/FakeEventSource.ts`, `test/fixtures.ts`; `frontend/CLAUDE.md`; `test/smoke_frontend.sh`
- **Exit criteria:** all five met. The three visual ones were verified by driving a real browser
  against a real server, not by reading the code
  - *`npm run build` produces a static export in `out/` with no errors* — and, after review finding
    1, **from a clean clone**: `git clone` of the branch, `npm ci`, build, `tsc`, 89 tests, all green
  - *Prices visibly stream and flash green/red, fading rather than sticking* — over 24 samples at
    250 ms, 118 up-flashes and 79 down-flashes observed across the grid. The fade is the second
    half: when the feed was killed, **every flash class cleared** (0 stuck) while the last prices
    stayed on screen
  - *Stopping the backend turns the dot yellow then red; restarting reconnects without a page
    reload* — observed in that order, `bg-accent`/"Reconnecting" → `bg-down`/"Disconnected", with
    the feed panel reading "No price stream. Prices below are the last received." On restart the
    server logged `SSE client connected` and the page returned to `bg-up`/"Live" with prices
    moving, `performance.getEntriesByType('navigation').length === 1` — one navigation, no reload.
    The accumulated sparklines survived the outage
  - *Sparklines accumulate progressively from page load* — 13 → 25 points over six seconds after a
    fresh load, one per 500 ms tick, every row drawing one. Empty at load, never fabricated
  - *Component tests cover render-with-mock-data and a flash class on price change* — both, plus
    the fade, plus flash isolation to the row that moved
- **Tests:** **89 frontend** (new) and 694 backend (+2). Both suites green three consecutive times;
  backend green a fourth time under coverage at **100%**, holding the floor from Checkpoints 1–4.
  `npm run lint`, `tsc --noEmit`, `ruff check` and `ruff format --check` all clean

  **17 mutations run, 17 killed** — the single connection, `close()` and listener removal on
  unmount, the escalation timer's arming and clearing, CLOSED-versus-retrying, the grace period,
  the sparkline cap and accumulation, quote validation, both flash guards and the restart key,
  em-dash-not-zero, the unpriced-position rule, stream-beats-fetch in two places, and the provider
  itself. One survived the first pass and was a genuine gap, the **fifth checkpoint running**
  where that has been true: dropping the "first price is not a change" guard passed every test in
  the file, because they all start from a price rather than from nothing. A ticker added to the
  watchlist would have flashed green on being priced — reporting a gain that never happened.
  `"does not flash when a row receives its first price"` now covers it
- **Review:** `/code-review high` returned **5 findings, all 5 fixed**, each fix mutation-verified
  1. **HIGH — `frontend/src/lib/` was never committed.** The root `.gitignore` carries `lib/` from
     GitHub's Python template, and an unanchored pattern matches a directory of that name at *any*
     depth. Six files untracked; a clean clone could not build, typecheck or test, and every local
     check passed because the files were sitting on disk. The exit criterion was not satisfied by
     what had been committed. **Fixed**: the pattern is anchored to the repo root, and
     `test/smoke_frontend.sh` fails if anything under `frontend/src` is ignored or untracked
  2. **MEDIUM — the header's P&L divided a live numerator by a stale denominator.**
     `portfolio.cost_basis` covers the positions priced *at fetch time*; `valuePortfolio` marks
     every position it can price now. Holding MSFT at 4,000 priced and AAPL at 2,000 unpriced, the
     header read **+55%** where the truth was +3.7%, the instant AAPL's first quote arrived — and
     `makePortfolio` mirrors the backend, so no fixture could have caught it. **Fixed**:
     `LiveValuation` carries the cost of exactly the positions it marked
  3. LOW — a price going from a number to `null` left the flash class set for the session.
     **Fixed** by deriving it from the current price rather than clearing it in the effect; the
     obvious fix tripped `react-hooks/set-state-in-effect`, and the derivation is better anyway
  4. LOW — a frame whose every quote failed validation still advanced `frames` and `lastFrameAt`,
     so the feed panel reported "Streaming" beside a grid of dashes: the one distinction it exists
     to make. **Fixed**
  5. LOW — the error branch replaced a grid that `useApiResource` deliberately keeps through a
     failed reload, and those rows are still being marked by the stream. **Fixed**: a banner above
     the rows. Not reachable until Checkpoint 6 calls `reload()`, which is when it would have bitten

  The reviewer also confirmed the wire contract field-by-field against `PriceUpdate.to_dict`,
  `MarketEvent.to_dict` and `_row`, and cleared the `EventSource` lifecycle — this checkpoint's
  stated review focus — as sound.

  `/security-review` not run: optional here by the gate definition, and this checkpoint adds no
  untrusted input, no money movement and no secret handling. The one thing worth noting is that
  `NEXT_PUBLIC_API_BASE` is inlined into the bundle at build time, so it must never hold anything
  secret; it holds a localhost URL for `next dev` and is empty in production.

  **Structure pass, done inline.** `usePriceStream` opens a connection per call, so "one
  `EventSource` per page" was true only because `page.tsx` happened to call it once — a panel
  reaching for prices in Checkpoint 6 would have silently doubled the streams the backend feeds,
  and nothing in the hook's signature would have stopped it. `TerminalProvider` is now the only
  caller. It also answers the account half: a trade from Checkpoint 6's trade bar and an
  auto-executed trade from Checkpoint 7's chat both need every panel to agree again, and
  `refresh()` is that one call
- **Diverged from plan:** four, all deliberate
  - **`FeedPanel` is not in §10's component list.** Checkpoint 5 fills the header and one panel,
    which leaves the main column empty until Checkpoint 6, and §10's alternative was placeholder
    text that Checkpoint 10 forbids. It shows feed health — frames, last update, tickers priced —
    and consumes the `event: shock` frames the backend has published since Checkpoint 1 with
    nothing reading them. It also carries the "prices below are the last received" line that makes
    a dead feed legible
  - **No webfont.** §10 does not require one and `create-next-app` supplies two; `next/font/google`
    fetches at build time, which would put a network dependency inside Checkpoint 8's
    `docker build`. The system stack has no swap flash and already has tabular figures
  - **`NEXT_PUBLIC_API_BASE` is a new environment variable**, added to §5. It is empty in
    production — the export and the API share an origin — and exists because `output: 'export'`
    ignores `rewrites`, so `next dev` on :3000 has no other way to reach :8000
  - **The backend test fixture now pins `STATIC_DIR`.** Not planned, and not optional: see below
- **The suite was environment-dependent, and this checkpoint proved it.** Building the frontend
  made `frontend/out` exist, `_resolve_static_dir()` found it, and the *test* app began mounting
  `StaticFiles` at "/" — which answers every unmatched `/api/*` path. A watchlist test asserting
  404-or-422 on an encoded-traversal path started returning 405 from the static handler.
  `clean_environment` listed `STATIC_DIR` among the variables it cleared, but clearing it is not
  neutral when the default is a filesystem search. It is now pinned to a path that cannot exist,
  with `test_the_suite_itself_runs_with_no_static_mount` as the guard and
  `test_an_unmatched_api_path_reaches_no_handler_behind_the_frontend` recording what production
  actually does. Both fail with the pin removed. Until today, any developer who had built a
  frontend was running a different suite from CI
- **Resolved from Checkpoint 4's carried-forward list:** none of it belonged to this checkpoint.
  The unreproducible failure, the stalled-source gap, the ticker that stays subscribed, the mock's
  stop-list, `list_trades()`, the `_unsubscribe` race and the untested prompt behaviour are all
  unchanged and restated below
- **Carried forward:**
  - **There is no watchlist add/remove control, and no checkpoint's scope claims one.** `POST` and
    `DELETE /api/watchlist` have been live since Checkpoint 3 and §2 promises the user can manage
    the list by hand, but §Checkpoint 5 lists only the panel's columns and §Checkpoint 6 lists
    charts, the heatmap and the trade bar. **Checkpoint 6 should take it** — it is the checkpoint
    with the trade bar, and the two belong side by side
  - **Nothing renders `unpriced_tickers` except the header.** A position with no price is named
    there; Checkpoint 6's positions table and heatmap must not quietly show it as zero
  - **The main column is empty until Checkpoint 6.** Deliberate — an honest gap beats placeholder
    text — but it is the one thing that makes the page look unfinished, and §2's "every pixel
    earns its place" is not yet true
  - **The render path has not been measured under load.** Ten rows at 2 Hz is nothing, but every
    frame replaces the `prices` and `sparklines` objects and re-renders every consumer.
    Checkpoint 6's review focus is exactly this, and it arrives with a chart and a treemap
  - **`RECONNECT_GRACE_MS` is 6 s, chosen not derived.** Long enough that a one-second blip does
    not read as an outage, short enough that a dead backend does not stay amber. Nothing measures
    what a real reconnect costs
  - **The browser verification is manual.** Gate 3's flash, dot and sparkline evidence came from
    driving Playwright by hand; `test/smoke_frontend.sh` covers everything else and is re-runnable.
    **Checkpoint 9 owns this** — those three observations are exactly its SSE-resilience and
    fresh-start scenarios
  - **`.playwright-mcp/` is written into the repo root** by the MCP browser tooling and was deleted
    by hand after Gate 3. If Checkpoint 9 drives a browser the same way, that path needs a
    `.gitignore` entry
  - Frontend coverage is **not measured** — no coverage provider is installed, and the status
    table's figure is the backend's by its own definition. Checkpoint 10 should decide whether the
    frontend needs a floor of its own
  - Mutation testing found a real gap for the **fifth checkpoint running**. The lesson has now held
    across two languages and two test frameworks

##### Checkpoint 5 — follow-up pass (2026-08-12 · branch `fix-stale-feed-and-flake` · PR #9)

*Written while PR #8 was still open and merged before it landed, so it went to
`main` as its own PR rather than as part of Checkpoint 5.*

An audit of everything Checkpoints 1–5 carried forward, run against the live
app rather than against the list. Two of the carried-forward items were real
defects and are now closed; the rest are restated below with what changed.

- **The stalled-but-alive market source is fixed.** Carried forward unresolved
  from Checkpoint 3 and again from Checkpoint 4, both times because "the
  threshold has to come from the poll interval" and guessing it would block
  valid trades. The threshold now comes from the source, which is the only
  thing that knows it:
  - `MarketDataSource.quote_staleness_limit` on the ABC. The simulator answers
    `max(interval * 20, 5 s)`, Massive `max(interval * 4, 60 s)`
  - **The bound is on receipt time, not on `PriceUpdate.timestamp`.** That was
    the trap behind the two deferrals: the venue timestamp is hours old the
    moment the market closes, so bounding it would refuse every trade out of
    hours. `PriceCache` now records when *it* was written, on the monotonic
    clock, and `age_of` / `is_stale` answer from that
  - `start()` stamps the limit on the cache it is about to write, rather than
    the call sites doing it. There are two call sites — `start_market_data` and
    the lifespan's failover handler — and the second is the one that would have
    been forgotten: a simulator taking over from a dead Massive must install
    its own 10-second bound, not inherit a 60-second one
  - `app.portfolio._require_price` refuses the fill, so the chat path is
    covered by the same rule as the trade bar. `get_portfolio` is untouched:
    only trading is refused, and a blank portfolio would be worse than a stale
    one
  - **10 mutations, 10 killed**, including both halves of the stamping, both
    bounds, and the trade path forgetting to ask
- **Checkpoint 4's unreproducible test failure is identified and fixed.** It is
  `tests/test_main.py::TestSnapshotTask::test_keeps_appending_every_interval`,
  reproduced here at roughly **one run in seventeen** and captured with full
  output this time. The mechanism: it slept 50 ms and expected two snapshots,
  each of which opens a SQLite connection. It is **I/O latency, not CPU** —
  twelve `yes` processes never reproduced it, and slowing the write to 30 ms
  reproduces it **5 times out of 5**.
  - `tests/conftest.wait_until` waits for a condition instead of a duration,
    and eleven fixed-sleep assertions across `test_main.py`,
    `test_simulator_source.py` and `test_massive.py` — every one of the form
    "sleep, then assert N things happened" — now use it. The inverse form,
    "assert nothing happened after shutdown", keeps its fixed sleep: there is
    no condition to wait for and a longer sleep only makes it stronger
  - `test_keeps_appending_even_when_each_write_is_slow` is the regression
    guard, and the controlled experiment: with a 30 ms write the old form fails
    5/5 and the new one passes 5/5
  - **25 consecutive full-suite runs clean**, against a defect that showed at
    about 1 in 17
- **The dot no longer lies about a wedged feed.** The UI half of the same
  defect: a wedged source keeps the SSE connection open and healthy, so the
  page read "Live" over frozen numbers — the exact distinction `FeedPanel`'s
  docstring claims to make. `usePriceStream` now reports `stalled` after
  `STALL_AFTER_MS` (30 s) without a frame, on one interval for the life of the
  mount rather than a timeout rescheduled per frame, cleared on unmount. The
  dot shows amber "Stalled" and the panel says the values are frozen. A real
  disconnection still wins over a stall. **7 mutations, 7 killed**
- **`.playwright-mcp/` is now ignored**, closing Checkpoint 5's own note about
  the browser tooling writing into the repo root
- **Tests:** backend 694 → **717**, frontend 89 → **98**. Both suites green
  three consecutive times, backend a fourth under coverage at **100%**, and 25
  more full runs while hunting the flake. `ruff`, `eslint` and `tsc` clean
- **Not fixed, and why.** `list_trades()` is still uncalled — it is scaffolding
  with a named owner (Checkpoint 6 or 7 surfaces it, Checkpoint 10 deletes it),
  not a defect. The `_unsubscribe` race is unchanged: closing it needs the
  source subscription and the database row under one lock, which the async/sync
  split prevents, and it is narrowed rather than open. A ticker still stays
  subscribed after its position closes, which remains deliberate. The watchlist
  add/remove UI is missing rather than broken and belongs to Checkpoint 6
- **Carried forward, restated:**
  - `MASSIVE_POLL_INTERVAL` above 30 seconds would make the frontend's
    `STALL_AFTER_MS` fire on a healthy feed. The backend derives its bound from
    the interval; the frontend cannot see it. If a deployment ever needs a slow
    poll, the limit has to reach the client — an `event: status` field would do
    it
  - The staleness bound is not exercised end-to-end against a running server.
    It is unit-tested, contract-tested across both sources, mutation-verified,
    and driven once through a real `SimulatorDataSource` whose loop was taken
    away — but wedging a live process from outside is exactly what Checkpoint 9
    should automate
  - Everything else on Checkpoint 4's and Checkpoint 5's lists is unchanged

#### Checkpoint 6 — Charts, portfolio visualisation, trade bar

- **Closed:** 2026-08-14 · branch `checkpoint-6-charts-portfolio-trade` · PR #10 · all four gates
  passed. Gate 3 failed twice and returned to Gate 2 both times, exactly as the gate rules require
- **Built:** `frontend/src/` — six new panels, the account mutations behind them, and the treemap
  - `components/LineChart.tsx` (**new**) — the one plot both time series use. SVG in a fixed 0–100
    box stretched with `preserveAspectRatio="none"`, labels as HTML over it, y axis on the series'
    own range. No dependency, nothing to dispose of on unmount
  - `components/PriceChart.tsx` (**new**) — the main chart, plus the low/high/day the line cannot
    carry. Drawn from the same client-accumulated series as the sparklines
  - `components/PositionsTable.tsx` (**new**) — every §10 column, marked to the stream, flashing
    per row
  - `components/PortfolioHeatmap.tsx` (**new**) — squarified treemap, sized by weight, coloured by
    P&L sign with the magnitude in the intensity and clamped at 8%
  - `components/PnlChart.tsx` (**new**) — the `portfolio_snapshots` series, with `formatStamp`
  - `components/TradeBar.tsx` (**new**) — market orders, and the rejection made visible
  - `components/WatchlistPanel.tsx` — gains selection, the add form and a per-row remove
  - `lib/treemap.ts` (**new**) — `squarify`, Bruls/Huizing/van Wijk. Pure arithmetic, separated
    from the component precisely so "do the areas match the weights" is assertable
  - `lib/ticker.ts` (**new**) — `TICKER_PATTERN` mirrored from `app/market/models.py`
  - `lib/valuation.markPositions` — one implementation of "what is this holding worth right now",
    read by the table and the map alike; `valuePortfolio` now derives from it
  - `lib/api.ts` — `sendJson`, `watchlistEntryPath`, `describeError`, and a `readDetail` that
    understands FastAPI's 422 array; `lib/format.ts` — `formatSignedDollars`, `formatQuantity`
  - `state/TerminalProvider.tsx` — `trade` / `addTicker` / `removeTicker`, each refreshing every
    panel afterwards; the history series on a 30 s poll matching the backend's snapshot task; and
    loading/error split per resource
  - `hooks/useApiResource.ts` — an optional `refreshMs`, for the one endpoint that grows on the
    server's own clock; `hooks/usePriceStream.ts` — `MAX_SERIES_POINTS` 600 (the main chart draws
    the same buffer; `Sparkline` renders the tail) and `pricedTickers`
  - `app/page.tsx` — the three-column workstation, and selection
  - `test/mutate.py` — taught about the frontend: 20 new mutations, run by vitest in the same
    throwaway worktree
  - **No backend source changed at all.** Every endpoint this checkpoint consumes shipped in
    Checkpoints 3 and 4
- **Exit criteria:** all six met, the five visual ones by driving a real browser against a real
  `uvicorn` on a fresh database — not by reading the code
  - *Clicking a watchlist row selects that ticker in the main chart* — clicking TSLA moved the
    chart to TSLA at 249.87 with its own low/high, marked the row, and filled the trade bar
  - *A buy updates cash, positions, heatmap and header with no reload* — 12 TSLA at 249.80: cash
    10,000 → 7,002.40, a position row marked live at 250.11, a heatmap tile, the header total, and
    `performance.getEntriesByType('navigation').length === 1`
  - *The heatmap sizes by weight and colours by P&L sign, and survives an empty portfolio* — three
    positions tiled 55.41% / 36.84% / 7.75%, summing to exactly 100% of the box against market
    values of 3,003 / 1,997 / 420; TSLA `--color-up`, NVDA and MSFT `--color-down`. Empty renders
    "No positions yet. The map fills in as you buy."
  - *The P&L chart renders the series and extends as new snapshots arrive* — 23 → 24 → 25 points
    with no trade and no reload, from the backend's 30-second task via the client poll
  - *A rejected trade surfaces a visible error* — "Insufficient cash: AAPL x100000 at $189.93 costs
    $18,993,000.00, but only $4,584.44 is available." in `--color-down`, cash unmoved, the order
    kept on screen for correcting
  - *Adding a ticker starts it streaming; removing one drops the row* — PYPL added from the panel,
    priced at 186.86 within seconds with a sparkline accumulating; re-adding AAPL reported "AAPL is
    already on the watchlist." and kept the symbol; removing PYPL dropped the row with the
    positions intact and one navigation
- **Tests:** frontend 98 → **246**, backend 717 unchanged. Both suites green three consecutive
  times, backend a fourth under coverage at **100%**, holding the floor from Checkpoints 1–5.
  `npm run lint`, `tsc --noEmit`, `npm run build`, `ruff check` and `ruff format --check` clean.
  `test/smoke_frontend.sh` gained the Checkpoint 6 half — the panels in the export, the history
  endpoint, the fill, the four rejections the UI renders differently, and the watchlist round trip
  — and passes end to end

  **20 frontend mutations run, 20 killed.** Two survived the first pass, both genuinely vacuous —
  the **sixth checkpoint running** where that has been true:
  - *The trade bar's in-flight guard was never reached.* The `disabled` attribute was stopping the
    second click, so deleting `if (pending) return` changed nothing and no test noticed. Submitting
    the form directly is the path the attribute does not cover, and it is a real one — a form with
    inputs submits on Enter, and without the guard that is a duplicate order against live money
  - *The poll-interval cleanup was asserted through the fetch count, which cannot see it.* A leaked
    interval keeps firing, but `reload()` on an unmounted component sets state React discards, so
    no request is made either way. It now asserts on `vi.getTimerCount()`

  The backend's 37 mutations were re-run and all still killed
- **Review:** `/code-review high` — **6 findings (2 MEDIUM, 4 LOW), all 6 fixed**
  1. **MEDIUM** — the "charted ticker left the watchlist" fallback consulted `market.prices`, which
     is append-only by design, so a removed ticker kept its last quote forever and the chart stayed
     pinned to a frozen price with no row on screen to explain it. **The test that covered it was
     vacuous**: it was the one test in the file that emitted no SSE frames, so `prices` was empty
     and only the other branch ever ran. Selection now comes from the watchlist plus what is held —
     `app.watchlist.reconcile`'s tracked set — and the test emits frames first
  2. **MEDIUM** — `readDetail` only understood a string `detail`, but FastAPI serialises a 422 as an
     *array* of field reports, so every validation failure rendered as "422 Unprocessable Content".
     It now reads them as `field: message`, and both forms reject a symbol the pattern would refuse
     before spending a round trip (`lib/ticker.ts`). The docstring's claim that the frontend renders
     400 and 422 differently was corrected to what is true
  3. LOW — the main chart's tone spans 600 points and the sparkline's 120, so the comment claiming
     the two can never disagree was false. They can, and both are right; the comment says why
  4. LOW — the ticker field reverted to the charted symbol after a fill, so a user who typed NVDA,
     sold, then typed a new quantity and clicked Sell would have sold AAPL. Only the quantity clears
  5. LOW — one merged `error` field put a failed portfolio read over a watchlist of live streaming
     prices, and on a first load over the whole panel. Loading and error are now per resource
  6. LOW — "tickers priced" counted every ticker seen since page load, over-reporting after any
     removal — against the one distinction the feed panel exists to make. `pricedTickers` is the
     last frame's own count

  `/security-review` **run, though optional here by the gate definition** — this is the first client
  that initiates money movement. **No HIGH or MEDIUM findings.** Cleared: `watchlistEntryPath` is
  the only path built from user input and is `encodeURIComponent`-encoded; no `dangerouslySetInnerHTML`
  anywhere; server-supplied `detail` text and tickers reach the DOM only as JSX text or
  `aria-label`/`title`, all escaped by React; tile styles interpolate numbers and three literal
  `var(--color-*)` tokens, with `squarify` filtering non-finite weights; `TradeOrder` has no `price`
  field. `isTicker` is message quality, not a control — every symbol is re-validated at the FastAPI
  edge and again in `app.portfolio` / `app.watchlist`

  **Structure pass, inline** — the review had just covered the same diff. It confirmed the layering
  holds: `lib` ← `hooks` ← `components` ← `state`/`app`, with no component importing the provider
  and nothing in `lib` importing a component. Checkpoint 7 can add `sendChat` to `TerminalProvider`
  exactly as `trade` was added and get the refresh for free
- **Gate 3 failed twice, and both failures were invisible to the test suite.** jsdom performs no
  layout, and the prerendered export carries no geometry, so only a real browser could see them.
  Per the gate rules each returned to Gate 2 rather than being patched forward
  - **Every panel had collapsed to its content height.** A `section` is a flex *item* of its column
    wrapper, and a flex item stretches on the cross axis, not the main one — so the price chart was
    83px inside a 924px column and the plot 19px. The panels carry `h-full`, the watchlist gets a
    growing wrapper, and the centre column weights the chart 3:2 against the positions table
  - **The plot was sized by its viewBox rather than by its box.** An `svg` is a replaced element
    with an intrinsic aspect ratio; given only `h-full` and a 1:1 viewBox the browser computed a
    square, so the series drew across 45% of a wide panel while the live-end marker — positioned in
    CSS, not in the viewBox — sat stranded to the right of where the line stopped. The plot now has
    its own container with both dimensions given. Measured after: 866 × 422 in a 922px container,
    the label gutter exactly 56px, the marker on the line's end to within 2px
- **Diverged from plan:** three, all now in the spec
  - **§10's "canvas-based charting library preferred" was not followed.** Hand-rolled SVG instead,
    for the reason §10 gave for wanting canvas: at 2 Hz a render is one coordinate string, and a
    library would have added a dependency, an imperative instance to dispose of on unmount — this
    checkpoint's stated review focus — and a jsdom shim. §10 rewritten to describe what exists,
    including the `h-full w-full` rule the second Gate 3 failure taught
  - **The watchlist add/remove control is Checkpoint 6's**, carried forward from Checkpoint 5. §10
    and this checkpoint's scope and exit criteria now say so
  - **`test/mutate.py` is no longer backend-only.** It takes `--project`, runs vitest for frontend
    mutations, and symlinks the installed `node_modules` into the worktree rather than reinstalling
  - No new environment variables, and no backend change of any kind
- **Resolved from Checkpoint 5's carried-forward list:** the watchlist add/remove UI is built; the
  positions table and heatmap name an unpriced holding rather than showing it as zero, and the
  header still does; the main column is no longer empty, so §2's "every pixel earns its place" is
  true for the first time. `list_trades()` is still uncalled — Checkpoint 6 was its last named
  owner before Checkpoint 7, and no §10 component displays a trade blotter
- **Carried forward:**
  - **The render path is still not measured under load.** Every frame replaces `prices` and
    `sparklines` and re-renders every consumer, and there are now six panels rather than two. It is
    visibly fine at ten tickers and 2 Hz on a laptop, and nothing profiles it. `markPositions` is
    memoised on `[portfolio, prices]`, which changes every frame by design
  - **`list_trades()` has now outlived two named owners.** Checkpoint 7 surfaces it or Checkpoint 10
    deletes it; there is no third checkpoint that would want it
  - **Layout is verified only by hand.** The two Gate 3 failures were both CSS, both invisible to
    jsdom, and the guard added for the second is a class assertion rather than a measurement.
    **Checkpoint 9 owns this** — a browser assertion on the rendered plot width is one line in a
    Playwright spec and would have caught both
  - **`HISTORY_REFRESH_MS` is pinned to the backend's `SNAPSHOT_INTERVAL_SECONDS` by hand.** Two
    constants in two languages with no test tying them together; if the backend's changes, the P&L
    chart silently lags
  - **Frontend coverage is still not measured** — no provider is installed, and the status table's
    figure remains the backend's by definition. Checkpoint 10 should decide whether the frontend
    needs a floor of its own
  - The `_unsubscribe` race, a ticker staying subscribed after its position closes, the mock's
    stop-list heuristic, and the untested prompt behaviour are all unchanged from Checkpoint 5
  - Mutation testing found a real gap for the **sixth checkpoint running**, and for the second time
    in TypeScript
