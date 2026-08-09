"""SSE streaming endpoint for live price updates."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator, Callable

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache
from .events import EventLog

logger = logging.getLogger(__name__)


def create_stream_router(
    price_cache: PriceCache,
    *,
    interval: float = 0.5,
    heartbeat: float = 15.0,
    event_log: EventLog | None = None,
    status_provider: Callable[[], str | None] | None = None,
) -> APIRouter:
    """Build the SSE router.

    The router is created here rather than at module level so calling this
    twice — in tests, say — yields two independent routers instead of
    registering /prices twice on one shared router.
    """
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        """Live price stream.

            data: {"AAPL": {"ticker": "AAPL", "price": 190.50, ...}, ...}

        Plus two named event types: `shock` (a notable move) and `status`
        (market open/closed). Clients that ignore them still work.
        """
        return StreamingResponse(
            _generate_events(
                price_cache,
                request,
                interval=interval,
                heartbeat=heartbeat,
                event_log=event_log,
                status_provider=status_provider,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # defeat nginx response buffering
            },
        )

    return router


async def _generate_events(
    price_cache: PriceCache,
    request: Request,
    *,
    interval: float = 0.5,
    heartbeat: float = 15.0,
    event_log: EventLog | None = None,
    status_provider: Callable[[], str | None] | None = None,
) -> AsyncGenerator[str, None]:
    """Yield SSE frames until the client disconnects."""
    yield "retry: 1000\n\n"  # EventSource reconnects after 1 s

    last_version = -1
    last_status: str | None = None
    cursor = event_log.cursor if event_log is not None else 0  # start at 'now'
    last_sent = time.monotonic()
    client_ip = request.client.host if request.client else "unknown"
    logger.info("SSE client connected: %s", client_ip)

    try:
        while True:
            if await request.is_disconnected():
                logger.info("SSE client disconnected: %s", client_ip)
                break

            sent = False

            # 1. Price snapshot — only when something actually moved.
            version = price_cache.version
            if version != last_version:
                last_version = version
                prices = price_cache.get_all()
                if prices:
                    payload = json.dumps({t: u.to_dict() for t, u in prices.items()})
                    yield f"data: {payload}\n\n"
                    sent = True

            # 2. Notable moves, per-client cursor so every client sees each one.
            if event_log is not None:
                cursor, fresh = event_log.since(cursor)
                for event in fresh:
                    yield f"event: shock\ndata: {json.dumps(event.to_dict())}\n\n"
                    sent = True

            # 3. Market status transitions (None under the simulator).
            #
            # The provider is caller-supplied, and this generator's only other
            # handler is CancelledError — an exception here would escape
            # mid-body, abort the response, and leave EventSource reconnecting
            # into the same crash forever. Cheap insurance for a callback we
            # do not control.
            if status_provider is not None:
                try:
                    status = status_provider()
                except Exception:
                    logger.debug("Status provider failed; leaving status unchanged", exc_info=True)
                else:
                    if status != last_status:
                        last_status = status
                        yield f"event: status\ndata: {json.dumps({'market': status})}\n\n"
                        sent = True

            # 4. Comment-only heartbeat so idle proxies don't drop the connection.
            now = time.monotonic()
            if sent:
                last_sent = now
            elif now - last_sent >= heartbeat:
                yield ": keep-alive\n\n"
                last_sent = now

            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("SSE stream cancelled for: %s", client_ip)
