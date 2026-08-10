"""FastAPI application entry point.

One process serves everything (PLAN.md §3): the REST API under /api, the SSE
price stream, and the exported frontend as static files at the root.

    uv run uvicorn app.main:app --reload
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import health_router, portfolio_router, watchlist_router
from app.db import load_tracked_tickers
from app.market import (
    EventLog,
    PriceCache,
    create_simulator_source,
    create_stream_router,
    start_market_data,
)
from app.paths import BACKEND_DIR, REPO_ROOT, is_source_checkout
from app.portfolio import record_snapshot

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

#: Where the built frontend is looked for, in order, when STATIC_DIR is unset.
#: `backend/static/` is where the Dockerfile drops the Next.js export;
#: `frontend/out/` is where `npm run build` leaves it during local development,
#: and is only a meaningful guess inside a source checkout.
_STATIC_CANDIDATES = (BACKEND_DIR / "static",) + (
    (REPO_ROOT / "frontend" / "out",) if is_source_checkout() else ()
)

#: How often the P&L series gains a point (PLAN.md §7). Deliberately not an
#: environment variable: nothing outside a test wants a different value, and
#: tests either pass `_snapshot_loop` their own interval or patch this constant,
#: which the lifespan reads at call time.
SNAPSHOT_INTERVAL_SECONDS = 30.0


def create_app() -> FastAPI:
    """Build an application instance.

    A factory rather than module-level wiring so each test gets its own cache,
    event log and source. Sharing one PriceCache across tests would let a
    simulator started by one test satisfy another test's assertions.
    """
    price_cache = PriceCache()
    event_log = EventLog()

    app = FastAPI(title="FinAlly", version="0.1.0", lifespan=_lifespan)

    # Set before startup as well as during it, so anything that reads the state
    # outside a lifespan — a unit test hitting /api/health with TestClient in
    # its non-context form — sees an initialised attribute rather than raising.
    app.state.price_cache = price_cache
    app.state.event_log = event_log
    app.state.market_source = None
    app.state.snapshot_task = None
    app.state.shutting_down = False

    app.include_router(health_router)
    app.include_router(portfolio_router)
    app.include_router(watchlist_router)
    app.include_router(
        create_stream_router(
            price_cache,
            event_log=event_log,
            # Read through app.state, not captured: failover replaces the
            # source object, and a captured reference would keep reporting the
            # dead one's status forever.
            status_provider=lambda: _current_market_status(app),
        )
    )

    _mount_static(app)
    return app


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start the market feed on boot, stop it on shutdown."""
    # Blocking SQLite call, deliberately not offloaded: nothing else is running
    # yet, and this is also what creates and seeds the database on first boot,
    # so it must finish before the first request is served.
    tickers = load_tracked_tickers()
    logger.info("Tracking %d tickers from the database", len(tickers))

    source = await start_market_data(
        app.state.price_cache,
        tickers,
        event_log=app.state.event_log,
    )
    source.on_permanent_failure = _make_failover_handler(app)
    app.state.market_source = source

    snapshot_task = asyncio.create_task(
        _snapshot_loop(app, SNAPSHOT_INTERVAL_SECONDS), name="portfolio-snapshots"
    )
    app.state.snapshot_task = snapshot_task

    try:
        yield
    finally:
        # Set before the first await: from here on no failover may install a
        # replacement, so shutdown has one source to stop and cannot leave a
        # freshly started simulator ticking behind it.
        app.state.shutting_down = True

        snapshot_task.cancel()
        # Awaited, not just cancelled: the task may be mid-write inside
        # run_in_threadpool, and returning from the lifespan before it unwinds
        # would let the interpreter tear down under an open transaction.
        await asyncio.gather(snapshot_task, return_exceptions=True)
        app.state.snapshot_task = None

        current = app.state.market_source
        app.state.market_source = None
        if current is not None:
            await current.stop()


async def _snapshot_loop(app: FastAPI, interval: float) -> None:
    """Append a `portfolio_snapshots` row every `interval` seconds (PLAN.md §7).

    The first point is written straight away rather than one interval later, so
    a freshly started app draws a P&L chart with a line on it instead of thirty
    seconds of blank axes. Trades write their own point as part of the trade
    transaction, so the series has resolution where it matters regardless of
    where the tick happens to fall.

    The write is blocking SQLite, so it goes to a thread — this task shares an
    event loop with the simulator tick and every open SSE stream.

    One failure costs one point, not the series: the loop logs and carries on.
    Only cancellation ends it, and `CancelledError` is a `BaseException`, so it
    passes through the handler below untouched.

    `interval` is required rather than defaulted from the module constant: a
    default argument binds at def time, so patching `SNAPSHOT_INTERVAL_SECONDS`
    would not reach it, and a test that patched it would silently run at the
    real 30 seconds and assert nothing in its 50 ms window. The lifespan names
    the constant instead, which reads it at call time.
    """
    while True:
        try:
            await run_in_threadpool(record_snapshot, app.state.price_cache)
        except Exception:
            logger.exception("Portfolio snapshot failed; the series will resume next tick")
        await asyncio.sleep(interval)


def _current_market_status(app: FastAPI) -> str | None:
    source = app.state.market_source
    return source.market_status if source is not None else None


def _make_failover_handler(app: FastAPI) -> Callable[[Exception], Awaitable[None]]:
    """Build the callback a source invokes when it fails unrecoverably.

    Resolves CP1 review finding #2, which was deferred to this checkpoint
    because the lifespan it belongs in did not exist yet. Without it, a Massive
    key revoked mid-session stops the poller and freezes every price on screen
    with no error anywhere.
    """

    async def on_permanent_failure(exc: Exception) -> None:
        failed = app.state.market_source
        logger.error("Market data source failed permanently (%s) — switching to the simulator", exc)

        tickers = failed.get_tickers() if failed is not None else []

        try:
            fallback = create_simulator_source(app.state.price_cache, event_log=app.state.event_log)
            fallback.on_permanent_failure = on_permanent_failure
            await fallback.start(tickers)
        except Exception:
            # The caller is an `except` block in the failed source's task, so an
            # exception here escapes as nothing more than "Task exception was
            # never retrieved" — and leaves app.state pointing at the dead
            # source, which /api/health would go on reporting as the live one.
            logger.exception("Failover to the simulator failed; no market data source is running")
            app.state.market_source = None
            return

        if app.state.shutting_down:
            # Shutdown began while the replacement was starting. Nothing will
            # ever stop a source installed now, so retire it here instead.
            await fallback.stop()
            return

        app.state.market_source = fallback
        if failed is not None:
            await failed.stop()  # safe from in here: see MarketDataSource.on_permanent_failure
        logger.info("Simulator took over for %d tickers", len(tickers))

    return on_permanent_failure


def _resolve_static_dir() -> Path | None:
    """The directory holding the built frontend, or None if it isn't there.

    Absent is normal, not an error: the backend is developed and tested long
    before `npm run build` has ever run, and PLAN.md requires the API to work
    on its own in that state.
    """
    configured = os.environ.get("STATIC_DIR", "").strip()
    if configured:
        path = Path(configured)
        if path.is_dir():
            return path
        logger.warning("STATIC_DIR=%s is not a directory — serving the API only", configured)
        return None

    for candidate in _STATIC_CANDIDATES:
        if candidate.is_dir():
            return candidate
    return None


def _mount_static(app: FastAPI) -> None:
    """Serve the frontend at /, or a pointer to the API when it isn't built.

    Mounted last. StaticFiles at "/" matches every path, so mounting it before
    the routers would shadow /api/* entirely.
    """
    static_dir = _resolve_static_dir()
    if static_dir is not None:
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
        logger.info("Serving frontend from %s", static_dir)
        return

    logger.info("No frontend build found — serving the API only")

    @app.get("/", include_in_schema=False)
    def frontend_not_built() -> JSONResponse:
        return JSONResponse(
            {
                "status": "backend only",
                "detail": "No frontend build found. Run `npm run build` in frontend/.",
                "api": ["/api/health", "/api/stream/prices", "/docs"],
            }
        )


app = create_app()
