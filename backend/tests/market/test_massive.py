"""Tests for MassiveDataSource.

Fixtures come from TickerSnapshot.from_dict (see conftest.make_snapshot), not
MagicMock, so the tests fail when the code reads an attribute the real SDK
model does not have.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
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

from .conftest import make_snapshot


def _source(cache: PriceCache, tickers: list[str] | None = None) -> MassiveDataSource:
    """A source wired for polling without a real client."""
    source = MassiveDataSource(api_key="k", price_cache=cache, poll_interval=60.0)
    source._tickers = tickers if tickers is not None else ["AAPL"]
    source._client = object()  # only truthiness is checked
    return source


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
    @pytest.mark.parametrize(
        "message",
        [
            "401 Unauthorized",
            "403 Forbidden",
            "invalid api key",
            "NOT ENTITLED for this plan",
        ],
    )
    def test_permanent_failures(self, message):
        assert is_permanent_failure(Exception(message)) is True

    @pytest.mark.parametrize(
        "message",
        ["429 Too Many Requests", "503 upstream", "connection timed out"],
    )
    def test_transient_failures(self, message):
        """429 and 5xx must stay transient — the SDK already retries them."""
        assert is_permanent_failure(Exception(message)) is False


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
        source._fetch_snapshots = lambda: (_ for _ in ()).throw(
            Exception("401 Unauthorized: invalid API key")
        )

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

    async def test_last_poll_at_recorded(self):
        cache = PriceCache()
        source = _source(cache)
        source._fetch_snapshots = lambda: [make_snapshot()]
        assert source.last_poll_at is None
        await source._poll_once()
        assert source.last_poll_at is not None


@pytest.mark.asyncio
class TestLifecycle:
    async def test_start_polls_immediately(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="k", price_cache=cache, poll_interval=60.0)

        with patch("app.market.massive_client.RESTClient"):
            with patch.object(source, "_fetch_snapshots", return_value=[make_snapshot()]):
                await source.start(["AAPL"])

        assert cache.get_price("AAPL") == 190.5  # priced before start() returned
        await source.stop()

    async def test_start_normalises_tickers(self):
        """A lower-case row from SQLite must not silently yield no data —
        Massive tickers are case-sensitive."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="k", price_cache=cache, poll_interval=60.0)

        with patch("app.market.massive_client.RESTClient"):
            with patch.object(source, "_fetch_snapshots", return_value=[]):
                await source.start([" aapl ", "googl"])

        assert source.get_tickers() == ["AAPL", "GOOGL"]
        await source.stop()

    async def test_start_propagates_permanent_failure(self):
        """So the factory can fall back rather than poll a dead key forever."""
        source = MassiveDataSource(api_key="bad", price_cache=PriceCache())

        with patch("app.market.massive_client.RESTClient"):
            with patch.object(source, "_fetch_snapshots", side_effect=Exception("403 Forbidden")):
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

        with patch("app.market.massive_client.RESTClient"):
            with patch.object(source, "_fetch_snapshots", return_value=[make_snapshot()]):
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
        source._fetch_snapshots = lambda: (_ for _ in ()).throw(Exception("403 Forbidden"))

        await source._poll_loop()  # returns rather than looping forever

        assert len(notified) == 1
        assert isinstance(notified[0], PermanentMarketDataError)

    async def test_permanent_failure_without_a_callback_still_stops(self):
        source = MassiveDataSource(api_key="bad", price_cache=PriceCache(), poll_interval=0.01)
        source._tickers = ["AAPL"]
        source._client = object()
        source._fetch_snapshots = lambda: (_ for _ in ()).throw(Exception("401"))

        await source._poll_loop()  # must not raise

    async def test_transient_failures_keep_the_loop_running(self):
        source = MassiveDataSource(api_key="k", price_cache=PriceCache(), poll_interval=0.01)
        source._tickers = ["AAPL"]
        source._client = object()
        source._fetch_snapshots = lambda: (_ for _ in ()).throw(Exception("503 upstream"))

        task = asyncio.create_task(source._poll_loop())
        await asyncio.sleep(0.05)
        assert not task.done()  # still retrying
        task.cancel()

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
