"""Tests for the watchlist endpoints — PLAN.md §8.

The property under test throughout is the one MARKET_DATA_DESIGN.md §13.4
names: the database row and the market source's subscription are written by
one handler and never diverge. A row the source has not heard of is a ticker
that never gets a price, and a subscription with no row is a ticker nobody
asked for.
"""

from __future__ import annotations

import pytest

from app.api.schemas import WatchlistAddRequest
from app.api.watchlist import create_watchlist_entry
from app.db import connect, list_watchlist
from app.main import create_app
from app.watchlist import MAX_WATCHLIST_SIZE
from tests.conftest import PLAN_DEFAULT_WATCHLIST, collect_sse_frames, sse_data_frames


def stored_tickers() -> list[str]:
    with connect() as conn:
        return [entry.ticker for entry in list_watchlist(conn)]


class TestReadWatchlist:
    def test_returns_the_seeded_tickers_in_add_order(self, client):
        body = client.get("/api/watchlist").json()
        assert [row["ticker"] for row in body["tickers"]] == list(PLAN_DEFAULT_WATCHLIST)

    def test_attaches_a_quote_where_one_exists(self, client):
        rows = {row["ticker"]: row for row in client.get("/api/watchlist").json()["tickers"]}

        assert rows["AAPL"]["quote"]["price"] == 200.0
        assert rows["AAPL"]["quote"]["direction"] == "flat"
        assert rows["AAPL"]["added_at"]

    def test_reports_null_rather_than_zero_for_an_unpriced_ticker(self, client):
        """Null renders as an em dash. A zero renders as a stock that has gone
        to nothing, which is a different and much more alarming claim."""
        rows = {row["ticker"]: row for row in client.get("/api/watchlist").json()["tickers"]}
        assert rows["NFLX"]["quote"] is None


class TestAddTicker:
    def test_adds_the_row_and_subscribes_the_source(self, client, source):
        response = client.post("/api/watchlist", json={"ticker": "PYPL"})

        assert response.status_code == 201
        assert response.json()["ticker"] == "PYPL"
        assert "PYPL" in stored_tickers()
        assert source.added == ["PYPL"]

    def test_returns_the_quote_when_the_source_prices_immediately(self, client):
        """True of the simulator, which seeds a new ticker synchronously. On
        Massive the quote is null until the next poll — both are correct, and
        the frontend must handle either."""
        body = client.post("/api/watchlist", json={"ticker": "PYPL"}).json()
        assert body["quote"]["price"] == 50.0

    def test_normalises_the_ticker(self, client, source):
        body = client.post("/api/watchlist", json={"ticker": "pypl"}).json()

        assert body["ticker"] == "PYPL"
        assert stored_tickers().count("PYPL") == 1
        assert source.added == ["PYPL"]

    def test_a_duplicate_is_a_409(self, client, source):
        assert client.post("/api/watchlist", json={"ticker": "AAPL"}).status_code == 409
        assert stored_tickers().count("AAPL") == 1
        assert source.added == []

    def test_a_duplicate_in_another_case_is_still_a_duplicate(self, client):
        assert client.post("/api/watchlist", json={"ticker": "aapl"}).status_code == 409

    @pytest.mark.parametrize(
        "ticker", ["", "   ", "../../etc/passwd", "A" * 11, "AAPL; DROP TABLE watchlist", "1AAPL"]
    )
    def test_rejects_a_ticker_that_is_not_a_symbol(self, client, ticker):
        assert client.post("/api/watchlist", json={"ticker": ticker}).status_code == 422
        assert stored_tickers() == list(PLAN_DEFAULT_WATCHLIST)

    def test_rolls_the_row_back_when_the_source_refuses(self, client, source):
        """Database first, source second — so the failure to guard against is
        a committed row the source never accepted. It would show in the
        watchlist forever with no price and no way to explain why."""
        source.add_error = RuntimeError("subscription limit reached")

        response = client.post("/api/watchlist", json={"ticker": "PYPL"})

        assert response.status_code == 503
        assert "PYPL" not in stored_tickers()

    def test_is_a_503_when_no_source_is_running(self, sourceless_client):
        """After a failover that could not start a replacement. Adding a row
        nothing can price is worse than refusing."""
        response = sourceless_client.post("/api/watchlist", json={"ticker": "PYPL"})

        assert response.status_code == 503
        assert "PYPL" not in stored_tickers()


class TestWatchlistSizeCap:
    def test_an_add_past_the_cap_is_a_400_with_the_reason(self, client):
        """400, not 422: the request is well formed and the account cannot
        support it — PLAN.md §8's distinction, the same one an unaffordable
        trade earns. Checkpoint 3 carried this gap forward to Checkpoint 4."""
        for n in range(MAX_WATCHLIST_SIZE - len(PLAN_DEFAULT_WATCHLIST)):
            assert client.post("/api/watchlist", json={"ticker": f"F{n:03d}"}).status_code == 201

        response = client.post("/api/watchlist", json={"ticker": "PYPL"})

        assert response.status_code == 400
        assert "full" in response.json()["detail"]
        assert str(MAX_WATCHLIST_SIZE) in response.json()["detail"]


class TestRemoveTicker:
    def test_removes_the_row_and_unsubscribes_the_source(self, client, source):
        response = client.delete("/api/watchlist/AAPL")

        assert response.status_code == 200
        assert response.json() == {"ticker": "AAPL", "removed": True, "still_tracked": False}
        assert "AAPL" not in stored_tickers()
        assert source.removed == ["AAPL"]

    def test_accepts_a_lower_case_ticker(self, client, source):
        assert client.delete("/api/watchlist/aapl").status_code == 200
        assert source.removed == ["AAPL"]

    def test_a_ticker_that_is_not_watched_is_a_404(self, client, source):
        assert client.delete("/api/watchlist/PYPL").status_code == 404
        assert source.removed == []

    def test_keeps_the_position_and_the_subscription_when_the_ticker_is_held(
        self, client, source, add_position
    ):
        """Removing a ticker from the watchlist is not a sale, and the tracked
        set is `union(watchlist, positions)`. Dropping the price of a held
        ticker would make the portfolio total silently lose that position."""
        add_position("AAPL", quantity=4)

        body = client.delete("/api/watchlist/AAPL").json()

        assert body["still_tracked"] is True
        assert source.removed == []
        assert "AAPL" in source.get_tickers()

        portfolio = client.get("/api/portfolio").json()
        assert [p["ticker"] for p in portfolio["positions"]] == ["AAPL"]
        assert portfolio["positions"][0]["current_price"] == 200.0

    def test_resubscribes_when_the_ticker_is_bought_mid_removal(self, client, source, add_position):
        """The held check and the unsubscribe cannot share a transaction — one
        is SQLite, the other an await — so a buy can land between them. Left
        alone, that position has no price source: null mark, excluded from the
        total, for the life of the process.

        The buy is forced into exactly that window by creating the position
        from inside `remove_ticker`.
        """
        original = source.remove_ticker

        async def buy_while_unsubscribing(ticker):
            await original(ticker)
            add_position(ticker, quantity=3)

        source.remove_ticker = buy_while_unsubscribing

        body = client.delete("/api/watchlist/AAPL").json()

        assert body["still_tracked"] is True
        assert "AAPL" in source.get_tickers()
        assert client.get("/api/portfolio").json()["unpriced_tickers"] == []

    def test_restores_the_row_when_the_source_refuses_to_unsubscribe(self, client, source):
        """Symmetric with the add path. The row is already committed gone, so
        leaving it that way would have the watchlist and the source disagree
        with no way back."""

        async def refuse(ticker):
            raise RuntimeError("source is wedged")

        source.remove_ticker = refuse

        response = client.delete("/api/watchlist/AAPL")

        assert response.status_code == 503
        assert "AAPL" in stored_tickers()

    @pytest.mark.parametrize("ticker", ["A" * 11, "1AAPL", "AAPL*"])
    def test_rejects_a_ticker_that_is_not_a_symbol(self, client, ticker):
        assert client.delete(f"/api/watchlist/{ticker}").status_code == 422

    def test_a_traversal_style_path_never_reaches_the_handler(self, client, source):
        """An encoded slash does not match the route at all, so this is a 404
        from routing rather than a 422 from validation. Either answer is fine;
        what must not happen is the string reaching the database layer."""
        response = client.delete("/api/watchlist/..%2f..%2fetc")

        assert response.status_code in (404, 422)
        assert stored_tickers() == list(PLAN_DEFAULT_WATCHLIST)
        assert source.removed == []

    def test_is_a_503_when_no_source_is_running(self, sourceless_client):
        assert sourceless_client.delete("/api/watchlist/AAPL").status_code == 503
        assert "AAPL" in stored_tickers(), "the row must not go without the subscription"


class TestStreamsImmediately:
    async def test_a_ticker_added_at_runtime_appears_in_the_price_stream(self):
        """Checkpoint 3 exit criterion, against the real simulator and the real
        SSE generator — no stub source, no restart.

        The handler is called directly rather than over HTTP because TestClient
        cannot be driven from inside a running event loop, and the lifespan has
        to be running for there to be a real source at all. Routing and
        validation for this endpoint are covered above.
        """
        app = create_app()
        async with app.router.lifespan_context(app):
            source = app.state.market_source
            cache = app.state.price_cache
            assert "PYPL" not in cache

            await create_watchlist_entry(WatchlistAddRequest(ticker="PYPL"), source, cache)

            assert "PYPL" in source.get_tickers()
            assert "PYPL" in stored_tickers()
            payloads = sse_data_frames(await collect_sse_frames(cache, ticks=2))

        assert payloads[0]["PYPL"]["price"] > 0

    async def test_a_removed_ticker_stops_being_streamed(self):
        from app.api.watchlist import remove_watchlist_entry

        app = create_app()
        async with app.router.lifespan_context(app):
            source = app.state.market_source
            cache = app.state.price_cache
            assert "AAPL" in cache

            await remove_watchlist_entry("AAPL", source)

            assert "AAPL" not in source.get_tickers()
            payloads = sse_data_frames(await collect_sse_frames(cache, ticks=2))

        assert "AAPL" not in payloads[0]
