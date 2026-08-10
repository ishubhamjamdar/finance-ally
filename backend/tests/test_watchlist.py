"""Tests for `app.watchlist` — the tracked-set rules.

Driven against the domain functions, not HTTP, because that is how Checkpoint
4's chat handler will call them. Everything asserted here protects a watchlist
change the LLM makes just as much as one the user clicks.
"""

from __future__ import annotations

import pytest

from app.db import connect, list_watchlist
from app.watchlist import (
    MarketSourceUnavailableError,
    TickerAlreadyWatchedError,
    TickerNotWatchedError,
    WatchlistError,
    add,
    reconcile,
    remove,
)
from tests.conftest import PLAN_DEFAULT_WATCHLIST, RecordingSource


@pytest.fixture
def source(price_cache):
    """A source already in sync with the seeded watchlist, so each test's
    reconcile records only the change it made."""
    return RecordingSource(price_cache, tickers=list(PLAN_DEFAULT_WATCHLIST))


def stored() -> list[str]:
    with connect() as conn:
        return [entry.ticker for entry in list_watchlist(conn)]


class TestAdd:
    async def test_writes_the_row_and_subscribes_the_source(self, source):
        entry = await add(source, "PYPL")

        assert entry.ticker == "PYPL"
        assert "PYPL" in stored()
        assert source.added == ["PYPL"]

    async def test_normalises_the_ticker(self, source):
        assert (await add(source, " pypl ")).ticker == "PYPL"
        assert source.added == ["PYPL"]

    async def test_a_duplicate_raises_and_touches_nothing(self, source):
        with pytest.raises(TickerAlreadyWatchedError, match="already on the watchlist"):
            await add(source, "aapl")

        assert stored().count("AAPL") == 1
        assert source.added == []

    async def test_rolls_the_row_back_when_the_source_refuses(self, source):
        """Database first, source second — so the failure to guard against is a
        committed row the source never accepted. It would show in the watchlist
        forever with no price and no way to explain why."""
        source.add_error = RuntimeError("subscription limit reached")

        with pytest.raises(MarketSourceUnavailableError):
            await add(source, "PYPL")

        assert "PYPL" not in stored()


class TestRemove:
    async def test_deletes_the_row_and_unsubscribes(self, source):
        assert await remove(source, "AAPL") is False  # no longer tracked

        assert "AAPL" not in stored()
        assert source.removed == ["AAPL"]

    async def test_a_ticker_that_is_not_watched_raises(self, source):
        with pytest.raises(TickerNotWatchedError, match="not on the watchlist"):
            await remove(source, "PYPL")

        assert source.removed == []

    async def test_keeps_a_held_ticker_subscribed(self, source, add_position):
        """Removing from the watchlist is not a sale, and the tracked set is
        `watchlist ∪ positions`. Dropping a held ticker's price would make the
        portfolio total silently lose that position."""
        add_position("AAPL", quantity=4)

        assert await remove(source, "AAPL") is True

        assert "AAPL" not in stored()
        assert source.removed == []
        assert "AAPL" in source.get_tickers()

    async def test_restores_the_row_when_the_source_refuses(self, source):
        async def refuse(ticker):
            raise RuntimeError("source is wedged")

        source.remove_ticker = refuse

        with pytest.raises(MarketSourceUnavailableError):
            await remove(source, "AAPL")

        assert "AAPL" in stored()

    async def test_resubscribes_a_ticker_bought_mid_removal(self, source, add_position):
        """The held check and the unsubscribe cannot share a transaction — one
        is SQLite, the other an await — so a buy can commit between them. Left
        alone, that position has no price source for the life of the process.

        The buy is forced into exactly that window by opening the position from
        inside `remove_ticker`.
        """
        original = source.remove_ticker

        async def buy_while_unsubscribing(ticker):
            await original(ticker)
            add_position(ticker, quantity=3)

        source.remove_ticker = buy_while_unsubscribing

        assert await remove(source, "AAPL") is True
        assert "AAPL" in source.get_tickers()


class TestReconcile:
    async def test_makes_the_source_match_watchlist_union_positions(
        self, price_cache, add_position
    ):
        add_position("PYPL", quantity=2)
        source = RecordingSource(price_cache, tickers=["AAPL", "STALE"])

        tracked = await reconcile(source)

        assert tracked == set(PLAN_DEFAULT_WATCHLIST) | {"PYPL"}
        assert set(source.get_tickers()) == tracked
        assert source.removed == ["STALE"]

    async def test_is_idempotent(self, source):
        first = await reconcile(source)
        source.added.clear()
        source.removed.clear()

        assert await reconcile(source) == first
        assert (source.added, source.removed) == ([], [])

    async def test_a_position_opened_during_the_removals_survives(self, price_cache, add_position):
        """`wanted` is re-read after the removals precisely so this cannot
        strand a holding."""
        source = RecordingSource(price_cache, tickers=[*PLAN_DEFAULT_WATCHLIST, "PYPL"])
        original = source.remove_ticker

        async def buy_while_removing(ticker):
            await original(ticker)
            if ticker == "PYPL":
                add_position("PYPL", quantity=1)

        source.remove_ticker = buy_while_removing

        tracked = await reconcile(source)

        assert "PYPL" in tracked
        assert "PYPL" in source.get_tickers()


def test_every_failure_is_a_watchlist_error():
    """The handlers and Checkpoint 4's chat path both catch the base class, so
    a subclass that escaped it would surface as a 500 rather than a message."""
    for error in (
        TickerAlreadyWatchedError,
        TickerNotWatchedError,
        MarketSourceUnavailableError,
    ):
        assert issubclass(error, WatchlistError)
