"""Health check — PLAN.md §8, used by the Docker HEALTHCHECK."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.db import DEFAULT_USER_ID, connect

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health(request: Request) -> JSONResponse:
    """Report whether the database and the market feed are usable.

    Declared `def`, not `async def`: the database probe is blocking, so FastAPI
    runs this in a worker thread rather than stalling the event loop that also
    drives the simulator tick and every open SSE stream.

    The probe is a real query. Reporting "ok" because the process is up is what
    lets a container sit healthy while serving `no such table` — and opening a
    connection is also what lazily creates the database, so a deleted file is
    rebuilt by the very check that would otherwise report the damage.
    """
    database_ok = True
    try:
        with connect() as conn:
            # The row, not just the query. `SELECT 1 ... LIMIT 1` succeeds and
            # returns None against an empty table, so a database that had its
            # schema created but never seeded would report perfect health while
            # being unable to serve a single portfolio request.
            row = conn.execute(
                "SELECT 1 FROM users_profile WHERE id = ?", (DEFAULT_USER_ID,)
            ).fetchone()
        if row is None:
            logger.error("Health check: no profile row for %r", DEFAULT_USER_ID)
            database_ok = False
    except Exception:
        logger.exception("Health check: database probe failed")
        database_ok = False

    source = getattr(request.app.state, "market_source", None)
    body = {
        "status": "ok" if database_ok else "degraded",
        "database": "ok" if database_ok else "error",
        "market_data": {
            "source": type(source).__name__ if source is not None else None,
            "tickers": len(source.get_tickers()) if source is not None else 0,
            "market_status": source.market_status if source is not None else None,
        },
    }
    return JSONResponse(body, status_code=200 if database_ok else 503)
