# FinAlly — AI Trading Workstation

A visually stunning AI-powered trading workstation that streams live market data, simulates portfolio trading, and integrates an LLM chat assistant that can analyze positions and execute trades via natural language.

Built entirely by coding agents as a capstone project for an agentic AI coding course.

## Features

- **Live price streaming** via SSE with green/red flash animations
- **Simulated portfolio** — $10k virtual cash, market orders, instant fills
- **Portfolio visualizations** — heatmap (treemap), P&L chart, positions table
- **AI chat assistant** — analyzes holdings, suggests and auto-executes trades
- **Watchlist management** — track tickers manually or via AI
- **Dark terminal aesthetic** — Bloomberg-inspired, data-dense layout

## Architecture

Single Docker container serving everything on port 8000:

- **Frontend**: Next.js (static export) with TypeScript and Tailwind CSS
- **Backend**: FastAPI (Python/uv) with SSE streaming
- **Database**: SQLite with lazy initialization
- **AI**: LiteLLM → OpenRouter (Cerebras inference) with structured outputs
- **Market data**: Built-in GBM simulator (default) or Massive API (optional)

## Quick Start

```bash
# Clone and configure
cp .env.example .env
# Add your OPENROUTER_API_KEY to .env — everything else has a working default,
# and the app runs without it (simulated prices, no AI chat)

# Build and run: one command, and it opens the browser for you
scripts/start_mac.sh              # macOS/Linux  (.\scripts\start_windows.ps1 on Windows)
scripts/stop_mac.sh               # stops the container; your portfolio persists

# Or drive Docker yourself
docker build -t finally .
docker run -v finally-data:/app/db -p 127.0.0.1:8000:8000 --stop-timeout 15 \
    --env-file .env finally

# Or with Compose
docker compose up --build

# Open http://localhost:8000
```

The app is published on **loopback only** — it has no login, so binding every interface would put
your portfolio and your OpenRouter credits on the local network. Set `FINALLY_BIND=0.0.0.0` if you
genuinely want to reach it from another machine. `FINALLY_PORT` moves it off 8000.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | Yes | OpenRouter API key for AI chat |
| `MASSIVE_API_KEY` | No | Massive (Polygon.io) key for real market data; omit to use simulator |
| `LLM_MOCK` | No | Set `true` for deterministic mock LLM responses (testing) |

## Testing

```bash
# Backend unit tests
cd backend && uv run --extra dev pytest

# Frontend component tests
cd frontend && npm test

# End-to-end, in containers: the production image plus a Playwright runner.
# Needs no API key — it runs with LLM_MOCK=true and the market simulator.
test/e2e.sh          # one run
test/e2e.sh 3        # three consecutive runs
```

## Project Structure

```
finally/
├── frontend/    # Next.js static export
├── backend/     # FastAPI uv project
├── planning/    # Project documentation and agent contracts
├── test/        # Playwright E2E suite, smoke scripts, mutation harness
├── db/          # SQLite volume mount (runtime)
└── scripts/     # Start/stop helpers
```

## License

See [LICENSE](LICENSE).
