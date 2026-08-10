"""SSE endpoint tests.

MARKET_DATA_DESIGN.md §16.4 proposed driving these through
httpx.ASGITransport. That does not work: the SSE generator is infinite by
design, and ASGITransport does not deliver an `http.disconnect` message, so
`request.is_disconnected()` never returns True and closing the response hangs
forever. (Verified — the client blocks before even yielding the first frame.)

So the frame logic is driven directly through `_generate_events` with a stub
request that disconnects after a set number of ticks. That is deterministic,
needs no sleeps, and exercises exactly the code a real client would drive. The
HTTP wiring — route, media type, headers — is asserted separately off the
router itself.
"""

import asyncio
import json

import pytest
from fastapi.routing import APIRoute

from app.market import EventLog, PriceCache, create_stream_router
from app.market.models import MarketEvent
from app.market.stream import _generate_events

# Defined in tests/conftest.py so the watchlist tests can drive a real SSE
# frame too — "a ticker added at runtime appears in the stream" is a Checkpoint
# 3 exit criterion, and it deserves the actual frame, not a proxy for it.
from tests.conftest import StubStreamRequest, collect_sse_frames, sse_data_frames


@pytest.mark.asyncio
class TestFrames:
    async def test_opens_with_retry_directive(self):
        """Without it the browser's EventSource uses its own default backoff."""
        frames = await collect_sse_frames(PriceCache(), ticks=1)
        assert frames[0] == "retry: 1000\n\n"

    async def test_emits_price_snapshot(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)

        payloads = sse_data_frames(await collect_sse_frames(cache, ticks=2))
        assert payloads[0]["AAPL"]["price"] == 190.0
        assert payloads[0]["AAPL"]["direction"] == "flat"

    async def test_empty_cache_emits_no_snapshot(self):
        assert sse_data_frames(await collect_sse_frames(PriceCache(), ticks=3)) == []

    async def test_unchanged_cache_sends_nothing_new(self):
        """The version counter is what keeps a 15 s Massive poll from producing
        30 identical SSE frames."""
        cache = PriceCache()
        cache.update("AAPL", 190.0)

        assert len(sse_data_frames(await collect_sse_frames(cache, ticks=10))) == 1

    async def test_price_change_produces_a_new_frame(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)

        def bump(call: int) -> None:
            if call == 2:
                cache.update("AAPL", 191.0)

        payloads = sse_data_frames(await collect_sse_frames(cache, ticks=5, on_tick=bump))
        assert len(payloads) == 2
        assert payloads[0]["AAPL"]["price"] == 190.0
        assert payloads[1]["AAPL"]["price"] == 191.0
        assert payloads[1]["AAPL"]["direction"] == "up"

    async def test_day_change_reaches_the_wire(self):
        cache = PriceCache()
        cache.update("AAPL", 195.0, previous_close=190.0)

        payload = sse_data_frames(await collect_sse_frames(cache, ticks=2))[0]
        assert payload["AAPL"]["previous_close"] == 190.0
        assert payload["AAPL"]["day_change_percent"] == 2.6316


@pytest.mark.asyncio
class TestNamedEvents:
    async def test_shock_is_published(self):
        cache = PriceCache()
        log = EventLog()

        def shock(call: int) -> None:
            if call == 1:
                log.append(MarketEvent("TSLA", -3.4, 241.5))

        frames = await collect_sse_frames(cache, ticks=4, on_tick=shock, event_log=log)
        shocks = [f for f in frames if f.startswith("event: shock")]
        assert len(shocks) == 1

        payload = json.loads(shocks[0].split("data: ", 1)[1])
        assert payload["ticker"] == "TSLA"
        assert payload["magnitude_percent"] == -3.4

    async def test_backlog_is_skipped_on_connect(self):
        """A client joining an hour in should not be flooded with old shocks."""
        log = EventLog()
        log.append(MarketEvent("AAPL", 2.0, 190.0))

        frames = await collect_sse_frames(PriceCache(), ticks=3, event_log=log)
        assert [f for f in frames if f.startswith("event: shock")] == []

    async def test_each_shock_is_sent_once(self):
        log = EventLog()

        def shock(call: int) -> None:
            if call == 1:
                log.append(MarketEvent("TSLA", -3.4, 241.5))

        frames = await collect_sse_frames(PriceCache(), ticks=8, on_tick=shock, event_log=log)
        assert len([f for f in frames if f.startswith("event: shock")]) == 1

    async def test_status_emitted_once_until_it_changes(self):
        status = {"value": "closed"}

        def flip(call: int) -> None:
            if call == 3:
                status["value"] = "open"

        frames = await collect_sse_frames(
            PriceCache(), ticks=6, on_tick=flip, status_provider=lambda: status["value"]
        )
        statuses = [
            json.loads(f.split("data: ", 1)[1]) for f in frames if f.startswith("event: status")
        ]
        assert statuses == [{"market": "closed"}, {"market": "open"}]

    async def test_no_status_event_without_a_provider(self):
        """The simulator has no market hours, so the frontend gets no status."""
        frames = await collect_sse_frames(PriceCache(), ticks=3)
        assert [f for f in frames if f.startswith("event: status")] == []

    async def test_a_failing_status_provider_does_not_abort_the_stream(self):
        """This generator's only other handler is CancelledError, so an
        exception here would escape mid-body, abort the response, and put
        EventSource into an infinite reconnect loop with no prices. The obvious
        wiring — `lambda: source.market_status` — raises AttributeError whenever
        the active source is the simulator, which is the default."""
        cache = PriceCache()
        cache.update("AAPL", 190.0)

        def boom():
            raise AttributeError("'SimulatorDataSource' object has no attribute 'market_status'")

        frames = await collect_sse_frames(cache, ticks=4, status_provider=boom)

        assert sse_data_frames(frames)[0]["AAPL"]["price"] == 190.0  # prices still flow
        assert [f for f in frames if f.startswith("event: status")] == []


@pytest.mark.asyncio
class TestHeartbeat:
    async def test_heartbeat_when_idle(self):
        """Idle connections through a proxy get dropped without one."""
        frames = await collect_sse_frames(PriceCache(), ticks=3, heartbeat=0.0)
        assert [f for f in frames if f.startswith(": ")] == [": keep-alive\n\n"] * 3

    async def test_no_heartbeat_while_data_is_flowing(self):
        cache = PriceCache()

        def churn(call: int) -> None:
            cache.update("AAPL", 190.0 + call)

        frames = await collect_sse_frames(cache, ticks=4, on_tick=churn, heartbeat=0.0)
        assert [f for f in frames if f.startswith(": ")] == []

    async def test_heartbeat_suppressed_until_the_interval_elapses(self):
        frames = await collect_sse_frames(PriceCache(), ticks=3, heartbeat=3600.0)
        assert [f for f in frames if f.startswith(": ")] == []


@pytest.mark.asyncio
class TestCancellation:
    async def test_cancellation_exits_cleanly_instead_of_propagating(self):
        """A client vanishing mid-`await` cancels the task rather than setting
        `is_disconnected()`. The generator must absorb that and return, so the
        ASGI server sees a finished response instead of an escaping
        CancelledError.
        """
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        agen = _generate_events(cache, StubStreamRequest(ticks=10), interval=0)

        # Advance past the preamble to a yield inside the streaming loop —
        # cancellation lands wherever the generator is suspended, and only the
        # loop is guarded.
        assert await agen.asend(None) == "retry: 1000\n\n"
        assert (await agen.asend(None)).startswith("data: ")

        # Generator caught CancelledError and returned, so athrow reports
        # exhaustion. Were it re-raised, this would fail with CancelledError.
        with pytest.raises(StopAsyncIteration):
            await agen.athrow(asyncio.CancelledError())

    async def test_cancellation_is_logged(self, caplog):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        agen = _generate_events(cache, StubStreamRequest(ticks=10), interval=0)
        await agen.asend(None)
        await agen.asend(None)

        with caplog.at_level("INFO", logger="app.market.stream"):
            with pytest.raises(StopAsyncIteration):
                await agen.athrow(asyncio.CancelledError())

        assert "SSE stream cancelled" in caplog.text


class TestRouterWiring:
    def test_router_factory_returns_independent_routers(self):
        """The router is built inside the factory, so calling it twice does not
        register /prices twice on one shared module-level router."""
        cache = PriceCache()
        first = create_stream_router(cache)
        second = create_stream_router(cache)

        assert first is not second
        assert len(first.routes) == len(second.routes) == 1

    def test_route_is_mounted_at_the_documented_path(self):
        route = create_stream_router(PriceCache()).routes[0]
        assert isinstance(route, APIRoute)
        assert route.path == "/api/stream/prices"
        assert "GET" in route.methods

    @pytest.mark.asyncio
    async def test_response_declares_sse_content_type_and_headers(self):
        route = create_stream_router(PriceCache()).routes[0]
        response = await route.endpoint(StubStreamRequest(0))

        assert response.media_type == "text/event-stream"
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"
