# FinAlly Backend

FastAPI backend for the FinAlly AI Trading Workstation. One process serves the
REST API, the SSE price stream, and the exported frontend.

## Running

```bash
uv sync --extra dev
uv run uvicorn app.main:app --reload   # http://localhost:8000
```

The database is created and seeded on first use — there is no migration step.

## Structure

- `app/`
  - `main.py` - `create_app()`, lifespan (market feed start/stop, failover), static mount
  - `api/` - REST routers
    - `health.py` - `GET /api/health`
  - `db/` - SQLite layer
    - `schema.sql` - the six tables of PLAN.md §7
    - `database.py` - connection helper, lazy initialisation, seeding
  - `market/` - Market data subsystem (see `CLAUDE.md`)
    - `models.py` - PriceUpdate / MarketEvent dataclasses
    - `cache.py` - Thread-safe price cache
    - `events.py` - Bounded event log for simulator shocks
    - `interface.py` - MarketDataSource abstract interface
    - `simulator.py` - GBM-based market simulator
    - `massive_client.py` - Massive/Polygon.io API client
    - `factory.py` - Source selection, startup verification, fallback
    - `stream.py` - SSE streaming endpoint
    - `seed_prices.py` - Default tickers, seed prices, GBM parameters

- `tests/` - Unit tests, mirroring the package layout

## Running Tests

```bash
uv run --extra dev pytest                       # All tests
uv run --extra dev pytest --cov=app             # With coverage
uv run --extra dev pytest tests/db              # One directory
```

Tests never touch the real database: `tests/conftest.py` points `DB_PATH` at a
per-test temporary file and clears `MASSIVE_API_KEY`.

## Environment Variables

Every one has a working default: the app runs with no `.env` at all. The full
table — paths, logging, and the market data tuning knobs — is in `CLAUDE.md`,
kept in one place so the two documents cannot drift.

## Development

```bash
uv run --extra dev ruff check app/ tests/
uv run --extra dev ruff format app/ tests/
```
