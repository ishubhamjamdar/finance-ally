"""Tests for MassiveDataSource.

Fixtures come from TickerSnapshot.from_dict (see conftest.make_snapshot), not
MagicMock, so the tests fail when the code reads an attribute the real SDK
model does not have.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from massive.exceptions import AuthError
from massive.rest.models import SnapshotMarketType

from app.market.cache import PriceCache
from app.market.interface import PermanentMarketDataError
from app.market.massive_client import (
    MassiveDataSource,
    extract_previous_close,
    extract_price,
    extract_timestamp,
    is_permanent_failure,
    to_epoch_seconds,
)

from .conftest import (
    NOT_AUTHORIZED_BODY,
    RATE_LIMITED_BODY,
    UNKNOWN_KEY_BODY,
    make_snapshot,
    massive_source,
    offline_massive,
)


def _source(cache: PriceCache, tickers: list[str] | None = None) -> MassiveDataSource:
    """A source wired for polling without a real client."""
    return massive_source(cache, tickers=tickers)


class TestExtraction:
    """The ladder that makes this integration work across plan tiers."""

    def test_price_from_last_trade(self):
        """Developer+ : the actual last trade."""
        assert extract_price(make_snapshot(price=190.5)) == 190.5

    def test_price_falls_back_to_minute_bar(self):
        """Starter: no trades entitlement, so the latest minute bar."""
        snap = make_snapshot(price=188.25, with_trade=False)
        assert extract_price(snap) == 188.25

    def test_price_falls_back_to_day_bar(self):
        snap = make_snapshot(price=177.75, with_trade=False, with_min=False)
        assert extract_price(snap) == 177.75

    def test_price_falls_back_to_prev_day(self):
        """Pre-open: nothing has traded today."""
        snap = make_snapshot(with_trade=False, with_min=False, with_day=False)
        assert extract_price(snap) == 129.61

    def test_price_is_none_when_nothing_available(self):
        snap = make_snapshot(with_trade=False, with_min=False, with_day=False, with_prev_day=False)
        assert extract_price(snap) is None

    def test_no_lasttrade_timestamp_attribute(self):
        """The regression test for the bug that shipped: LastTrade exposes
        sip_timestamp. Reading `.timestamp` raised AttributeError on every
        snapshot, so the cache stayed empty while the app looked healthy."""
        snap = make_snapshot()
        assert not hasattr(snap.last_trade, "timestamp")
        assert snap.last_trade.sip_timestamp == 1675190399000000000

    def test_timestamp_lands_in_a_sane_range(self):
        ts = extract_timestamp(make_snapshot(ts_ns=1675190399000000000))
        assert 1.6e9 < ts < 1.8e9  # ns/1e3 would be year ~53,000,000

    def test_timestamp_falls_back_to_updated(self):
        snap = make_snapshot(with_trade=False)
        assert 1.6e9 < extract_timestamp(snap) < 1.8e9

    def test_previous_close_from_prev_day(self):
        assert extract_previous_close(make_snapshot()) == 129.61

    def test_previous_close_none_without_prev_day(self):
        assert extract_previous_close(make_snapshot(with_prev_day=False)) is None

    @pytest.mark.parametrize(
        "raw",
        [1675190399, 1675190399000, 1675190399000000, 1675190399000000000],
    )
    def test_epoch_normalisation_by_magnitude(self, raw):
        assert 1.6e9 < to_epoch_seconds(raw) < 1.8e9

    @pytest.mark.parametrize("raw", [None, 0, 1, 4e18])
    def test_epoch_normalisation_rejects_nonsense(self, raw):
        assert to_epoch_seconds(raw) is None


class TestFailureClassification:
    """The SDK raises BadResponse(body) and throws away resp.status, so these
    fixtures are real Polygon response bodies. Matching on "401"/"403" cannot
    work: a genuine 401 body contains neither.
    """

    @pytest.mark.parametrize(
        "body",
        [
            UNKNOWN_KEY_BODY,
            NOT_AUTHORIZED_BODY,
            '{"status":"ERROR","message":"Invalid API Key"}',
            "403 Forbidden",  # plain-text body from a proxy
        ],
    )
    def test_permanent_failures(self, body):
        assert is_permanent_failure(Exception(body)) is True

    @pytest.mark.parametrize(
        "body",
        [
            RATE_LIMITED_BODY,
            '{"status":"ERROR","message":"internal server error"}',
            "connection timed out",
        ],
    )
    def test_transient_failures(self, body):
        """Rate limits and 5xx must stay transient — the SDK already retries them."""
        assert is_permanent_failure(Exception(body)) is False

    def test_request_id_containing_a_status_code_is_not_permanent(self):
        """Every Polygon body carries a 32-char hex request_id. Matching markers
        against the whole string promotes a transient 429 to permanent roughly
        1.5% of the time — a poller that stops for good on a random hex run."""
        body = (
            '{"status":"ERROR","request_id":"403401aabbccddeeff00112233445566",'
            '"message":"You have exceeded the maximum requests per minute"}'
        )
        assert is_permanent_failure(Exception(body)) is False

    def test_auth_error_is_permanent(self):
        """AuthError means an empty key at construction — no retry fixes that."""
        assert is_permanent_failure(AuthError("no api key")) is True

    def test_malformed_body_falls_back_to_whole_text(self):
        assert is_permanent_failure(Exception("<html>403 Forbidden</html>")) is True

    def test_non_dict_json_body_is_handled(self):
        assert is_permanent_failure(Exception("[1, 2, 3]")) is False


@pytest.mark.asyncio
class TestPolling:
    async def test_poll_populates_cache_with_previous_close(self):
        cache = PriceCache()
        source = _source(cache)
        source._fetch_snapshots = lambda: [make_snapshot(price=190.5)]

        assert await source._poll_once() == 1
        update = cache.get("AAPL")
        assert update.price == 190.5
        assert update.previous_close == 129.61
        assert update.day_change_percent is not None

    async def test_poll_sets_timestamp_from_sip_timestamp(self):
        cache = PriceCache()
        source = _source(cache)
        source._fetch_snapshots = lambda: [make_snapshot(ts_ns=1675190399000000000)]

        await source._poll_once()
        assert cache.get("AAPL").timestamp == pytest.approx(1675190399.0)

    async def test_transient_error_keeps_previous_prices(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        source = _source(cache)
        source._fetch_snapshots = lambda: (_ for _ in ()).throw(Exception("503 upstream"))

        assert await source._poll_once() == 0  # does not raise
        assert cache.get_price("AAPL") == 190.0  # last known price retained

    async def test_permanent_error_raises(self):
        cache = PriceCache()
        source = _source(cache)
        source._fetch_snapshots = lambda: (_ for _ in ()).throw(Exception(UNKNOWN_KEY_BODY))

        with pytest.raises(PermanentMarketDataError):
            await source._poll_once()

    async def test_unpriceable_snapshot_skipped_others_processed(self):
        cache = PriceCache()
        source = _source(cache, ["AAPL", "BAD"])
        source._fetch_snapshots = lambda: [
            make_snapshot("AAPL", price=190.5),
            make_snapshot(
                "BAD", with_trade=False, with_min=False, with_day=False, with_prev_day=False
            ),
        ]

        assert await source._poll_once() == 1
        assert cache.get_price("AAPL") == 190.5
        assert cache.get("BAD") is None

    async def test_empty_tickers_skips_poll(self):
        cache = PriceCache()
        source = _source(cache, [])
        with patch.object(source, "_fetch_snapshots") as mock_fetch:
            assert await source._poll_once() == 0
            mock_fetch.assert_not_called()

    async def test_poll_without_client_is_noop(self):
        source = MassiveDataSource(api_key="k", price_cache=PriceCache())
        source._tickers = ["AAPL"]
        assert await source._poll_once() == 0

    async def test_one_bad_snapshot_does_not_lose_the_others(self):
        """Per-snapshot guarding: a malformed entry costs one ticker for one
        poll, not every ticker for the life of the process."""

        class Exploding:
            ticker = "BAD"

            @property
            def last_trade(self):
                raise ValueError("malformed field")

        cache = PriceCache()
        source = _source(cache, ["AAPL", "BAD", "GOOGL"])
        source._fetch_snapshots = lambda: [
            make_snapshot("AAPL", price=190.5),
            Exploding(),
            make_snapshot("GOOGL", price=175.0),
        ]

        assert await source._poll_once() == 2
        assert cache.get_price("AAPL") == 190.5
        assert cache.get_price("GOOGL") == 175.0
        assert cache.get("BAD") is None


@pytest.mark.asyncio
class TestLifecycle:
    async def test_start_polls_immediately(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="k", price_cache=cache, poll_interval=60.0)

        with offline_massive(snapshots=[make_snapshot()]):
            await source.start(["AAPL"])

        assert cache.get_price("AAPL") == 190.5  # priced before start() returned
        await source.stop()

    async def test_start_normalises_tickers(self):
        """A lower-case row from SQLite must not silently yield no data —
        Massive tickers are case-sensitive."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="k", price_cache=cache, poll_interval=60.0)

        with offline_massive():
            await source.start([" aapl ", "googl"])

        assert source.get_tickers() == ["AAPL", "GOOGL"]
        await source.stop()

    async def test_start_propagates_permanent_failure(self):
        """So the factory can fall back rather than poll a dead key forever."""
        source = MassiveDataSource(api_key="bad", price_cache=PriceCache())

        with offline_massive(error=NOT_AUTHORIZED_BODY):
            with pytest.raises(PermanentMarketDataError):
                await source.start(["AAPL"])

    async def test_stop_is_idempotent_and_safe_before_start(self):
        source = MassiveDataSource(api_key="k", price_cache=PriceCache())
        await source.stop()
        await source.stop()
        assert source._task is None

    async def test_stop_cancels_task(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="k", price_cache=cache, poll_interval=10.0)

        with offline_massive(snapshots=[make_snapshot()]):
            await source.start(["AAPL"])

        assert source._task is not None and not source._task.done()
        await source.stop()
        assert source._task is None

    async def test_add_and_remove_ticker_normalise(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="k", price_cache=cache)

        await source.add_ticker("  aapl  ")
        assert source.get_tickers() == ["AAPL"]

        await source.add_ticker("AAPL")  # duplicate is a no-op
        assert source.get_tickers() == ["AAPL"]

        cache.update("AAPL", 190.0)
        await source.remove_ticker("aapl")
        assert source.get_tickers() == []
        assert cache.get("AAPL") is None

    async def test_market_status_refresh_is_a_silent_noop_before_start(self, caplog):
        """`_poll_loop` refreshes status on a timer, but the client only exists
        after `start()`. Called before that it must return early.

        Asserting only "does not raise" would pass without the guard too — the
        AttributeError would just be swallowed by the catch-all below it. So
        assert the *silence*: no attempt was made, hence nothing to report.
        """
        source = MassiveDataSource(api_key="k", price_cache=PriceCache())
        assert source._client is None

        with caplog.at_level("DEBUG", logger="app.market.massive_client"):
            await source._refresh_market_status()

        assert source.market_status is None
        assert "Market status unavailable" not in caplog.text

    async def test_market_status_refresh_swallows_errors(self):
        source = MassiveDataSource(api_key="k", price_cache=PriceCache())
        source._client = object()  # has no get_market_status
        await source._refresh_market_status()  # must not raise
        assert source.market_status is None

    async def test_market_status_recorded_when_available(self):
        """Outside 09:30-16:00 ET real prices are static; surfacing the reason
        stops the UI looking broken."""
        source = MassiveDataSource(api_key="k", price_cache=PriceCache())
        source._client = SimpleNamespace(get_market_status=lambda: SimpleNamespace(market="open"))
        await source._refresh_market_status()
        assert source.market_status == "open"

    async def test_fetch_snapshots_delegates_to_the_sdk(self):
        source = MassiveDataSource(api_key="k", price_cache=PriceCache())
        source._tickers = ["AAPL", "GOOGL"]
        captured = {}

        def get_snapshot_all(market_type, tickers):
            captured["market_type"] = market_type
            captured["tickers"] = tickers
            return ["snap"]

        source._client = SimpleNamespace(get_snapshot_all=get_snapshot_all)
        assert source._fetch_snapshots() == ["snap"]
        assert captured["tickers"] == ["AAPL", "GOOGL"]
        assert captured["market_type"] == SnapshotMarketType.STOCKS


@pytest.mark.asyncio
class TestPollLoop:
    """Mid-run failure handling — delta #5. A broad `except Exception` used to
    retry a dead key every 15 s forever with no signal anywhere."""

    async def test_permanent_failure_stops_the_loop_and_fires_the_callback(self):
        cache = PriceCache()
        notified: list[Exception] = []

        async def on_failure(exc: Exception) -> None:
            notified.append(exc)

        source = MassiveDataSource(
            api_key="bad",
            price_cache=cache,
            poll_interval=0.01,
            on_permanent_failure=on_failure,
        )
        source._tickers = ["AAPL"]
        source._client = object()
        source._fetch_snapshots = lambda: (_ for _ in ()).throw(Exception(NOT_AUTHORIZED_BODY))

        # wait_for, not a bare await: if the classifier stops recognising this
        # body the loop runs forever, and a hung suite is a far worse failure
        # signal than an assertion.
        await asyncio.wait_for(source._poll_loop(), timeout=5)

        assert len(notified) == 1
        assert isinstance(notified[0], PermanentMarketDataError)

    async def test_stop_is_safe_from_inside_the_failure_callback(self):
        """The ABC lets a handler call stop() on the source that just failed —
        the obvious thing to do — even though the callback runs inside that
        source's own task. Honouring it is the source's job: it must release
        the task before awaiting the callback, or stop() cancels the very
        coroutine performing the failover.
        """
        stopped = []
        task_when_called = []

        source = MassiveDataSource(api_key="bad", price_cache=PriceCache(), poll_interval=0.01)

        async def on_failure(exc: Exception) -> None:
            task_when_called.append(source._task)
            await source.stop()
            # The real handler awaits `fallback.start()` here, so keep an await
            # after stop() — that is where a scheduled cancellation lands.
            await asyncio.sleep(0)
            stopped.append(exc)

        source.on_permanent_failure = on_failure
        source._tickers = ["AAPL"]
        source._client = object()
        source._fetch_snapshots = lambda: (_ for _ in ()).throw(Exception(NOT_AUTHORIZED_BODY))

        # The loop must run AS the task stop() would cancel — exactly how
        # start() launches it. Awaiting _poll_loop() directly leaves _task
        # unset, so stop() finds nothing to cancel and the test passes against
        # a source that never releases its task at all.
        task = asyncio.create_task(source._poll_loop())
        source._task = task
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=5)

        assert len(stopped) == 1, "the callback did not run to completion"
        assert task_when_called == [None], "the task was not released before the callback"
        # The decisive one. stop()'s own `except CancelledError` absorbs a
        # self-cancel, so the callback finishes either way and no amount of
        # asserting on its result can tell the two apart. cancelling() records
        # that a cancellation was *requested* at all — it stays 0 only if the
        # task really was released first, and a swallowed cancel never gets
        # uncancel()ed back down.
        assert task.cancelling() == 0, "stop() cancelled the task performing the failover"
        assert source._client is None  # the RESTClient's pool is released either way

    async def test_permanent_failure_without_a_callback_still_stops(self):
        source = MassiveDataSource(api_key="bad", price_cache=PriceCache(), poll_interval=0.01)
        source._tickers = ["AAPL"]
        source._client = object()
        source._fetch_snapshots = lambda: (_ for _ in ()).throw(Exception(UNKNOWN_KEY_BODY))

        await asyncio.wait_for(source._poll_loop(), timeout=5)  # must not raise

    async def test_transient_failures_keep_the_loop_running(self):
        source = MassiveDataSource(api_key="k", price_cache=PriceCache(), poll_interval=0.01)
        source._tickers = ["AAPL"]
        source._client = object()
        source._fetch_snapshots = lambda: (_ for _ in ()).throw(Exception("503 upstream"))

        task = asyncio.create_task(source._poll_loop())
        await asyncio.sleep(0.05)
        assert not task.done()  # still retrying
        task.cancel()

    async def test_unexpected_error_does_not_kill_the_poller(self):
        """Only PermanentMarketDataError should end the task. Anything else must
        be logged and retried — otherwise prices freeze with no fallback and the
        only trace is a 'Task exception was never retrieved' warning at GC."""
        source = MassiveDataSource(api_key="k", price_cache=PriceCache(), poll_interval=0.01)
        source._tickers = ["AAPL"]
        source._client = object()
        calls = {"n": 0}

        async def exploding_poll():
            calls["n"] += 1
            raise RuntimeError("unexpected")

        source._poll_once = exploding_poll

        task = asyncio.create_task(source._poll_loop())
        await asyncio.sleep(0.06)
        still_running = not task.done()
        task.cancel()

        assert still_running
        assert calls["n"] > 1  # retried rather than died on the first failure

    async def test_status_refreshed_on_the_configured_cadence(self):
        source = MassiveDataSource(
            api_key="k",
            price_cache=PriceCache(),
            poll_interval=0.001,
            status_refresh_polls=2,
        )
        source._tickers = ["AAPL"]
        source._client = object()
        source._fetch_snapshots = lambda: [make_snapshot()]
        refreshes = {"n": 0}

        async def counting_refresh():
            refreshes["n"] += 1

        source._refresh_market_status = counting_refresh

        task = asyncio.create_task(source._poll_loop())
        await asyncio.sleep(0.05)
        task.cancel()

        assert refreshes["n"] >= 1
