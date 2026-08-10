"""Tests for `app.watchlist` — the tracked-set rules.

Driven against the domain functions, not HTTP, because that is how Checkpoint
4's chat handler will call them. Everything asserted here protects a watchlist
change the LLM makes just as much as one the user clicks.
"""

from __future__ import annotations

import pytest

from app.db import add_watchlist_entry, connect, list_watchlist, transaction
from app.watchlist import (
    MAX_WATCHLIST_SIZE,
    MarketSourceUnavailableError,
    TickerAlreadyWatchedError,
    TickerNotWatchedError,
    WatchlistError,
    WatchlistFullError,
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


class TestSizeCap:
    """Checkpoint 3 shipped without a cap and carried it forward to here,
    because Checkpoint 4 hands `add` to a model that can call it in a loop and
    every entry joins every Massive poll thereafter."""

    async def fill_to_cap(self, source) -> None:
        for n in range(MAX_WATCHLIST_SIZE - len(PLAN_DEFAULT_WATCHLIST)):
            await add(source, f"F{n:03d}")

    async def test_adds_are_refused_at_the_cap(self, source):
        await self.fill_to_cap(source)
        assert len(stored()) == MAX_WATCHLIST_SIZE

        with pytest.raises(WatchlistFullError, match="full"):
            await add(source, "PYPL")

    async def test_the_refused_row_is_not_left_behind(self, source):
        """The check runs inside the insert's transaction, so the rollback is
        what un-does it. A committed row would put the list one over the cap
        and make the next add refuse for a ticker that is genuinely there."""
        await self.fill_to_cap(source)

        with pytest.raises(WatchlistFullError):
            await add(source, "PYPL")

        assert "PYPL" not in stored()
        assert len(stored()) == MAX_WATCHLIST_SIZE

    async def test_the_source_is_never_told_about_a_refused_add(self, source):
        await self.fill_to_cap(source)
        source.added.clear()

        with pytest.raises(WatchlistFullError):
            await add(source, "PYPL")

        assert source.added == []

    async def test_a_duplicate_at_the_cap_reports_the_duplicate(self, source):
        """A full list must not turn "AAPL is already watched" into "the list is
        full": re-adding a watched ticker adds nothing to poll, and the wrong
        message would send the user deleting things they did not need to."""
        await self.fill_to_cap(source)

        with pytest.raises(TickerAlreadyWatchedError):
            await add(source, "AAPL")

    async def test_removing_one_makes_room_again(self, source):
        await self.fill_to_cap(source)
        await remove(source, "AAPL")

        assert (await add(source, "PYPL")).ticker == "PYPL"

    async def test_a_compensating_restore_survives_a_concurrent_refill(self, price_cache):
        """`remove` puts the row back when the source refuses. Enforcing the cap
        on that restore turns a source failure into "the watchlist is full" —
        the wrong error — and leaves the row deleted after all, which is a
        compensating action losing the very thing it was compensating for.

        The refill is what makes this reachable, and is why the obvious version
        of this test was worthless: `remove` deletes before it restores, so on
        its own the restore only ever returns the list to the cap and never
        past it. Something else has to take the freed slot in between. That is
        exactly the window the review raised, and mutation testing is what
        showed the first version of this test could not fail.
        """
        source = RecordingSource(price_cache, tickers=list(PLAN_DEFAULT_WATCHLIST))
        await self.fill_to_cap(source)

        async def take_the_slot_then_fail(ticker):
            with transaction() as conn:
                add_watchlist_entry(conn, "TAKEN")
            raise RuntimeError("source is down")

        source.remove_ticker = take_the_slot_then_fail

        with pytest.raises(MarketSourceUnavailableError):
            await remove(source, "AAPL")

        assert "AAPL" in stored()
        assert len(stored()) == MAX_WATCHLIST_SIZE + 1

    async def test_the_last_slot_is_usable(self, source):
        """Off-by-one guard: the cap is the number of tickers allowed, not the
        number below which adds are allowed."""
        for n in range(MAX_WATCHLIST_SIZE - len(PLAN_DEFAULT_WATCHLIST) - 1):
            await add(source, f"F{n:03d}")

        assert len(stored()) == MAX_WATCHLIST_SIZE - 1
        await add(source, "PYPL")
        assert len(stored()) == MAX_WATCHLIST_SIZE


def test_every_failure_is_a_watchlist_error():
    """The handlers and Checkpoint 4's chat path both catch the base class, so
    a subclass that escaped it would surface as a 500 rather than a message."""
    for error in (
        TickerAlreadyWatchedError,
        TickerNotWatchedError,
        MarketSourceUnavailableError,
        WatchlistFullError,
    ):
        assert issubclass(error, WatchlistError)
