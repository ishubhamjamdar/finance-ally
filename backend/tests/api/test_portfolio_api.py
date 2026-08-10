"""Tests for the portfolio endpoints — PLAN.md §8.

The money rules themselves are covered in `tests/test_portfolio.py`. What is
asserted here is the HTTP contract on top of them: response shapes, and the
status code each failure earns. The distinction that matters is 422 versus 400
— a malformed request against a rejected one — because the frontend renders
them differently and the LLM in Checkpoint 4 has to tell them apart.
"""

from __future__ import annotations

import pytest

from app.portfolio import execute_trade


class TestReadPortfolio:
    def test_returns_the_seeded_account(self, client):
        body = client.get("/api/portfolio").json()

        assert body["cash_balance"] == 10000.0
        assert body["total_value"] == 10000.0
        assert body["positions"] == []
        assert body["unpriced_tickers"] == []

    def test_reflects_a_trade_without_a_restart(self, client):
        client.post("/api/portfolio/trade", json={"ticker": "AAPL", "side": "buy", "quantity": 3})

        body = client.get("/api/portfolio").json()
        assert body["cash_balance"] == 9400.0
        assert [p["ticker"] for p in body["positions"]] == ["AAPL"]
        assert body["positions"][0]["market_value"] == 600.0

    def test_marks_positions_to_the_live_cache(self, client, price_cache):
        client.post("/api/portfolio/trade", json={"ticker": "AAPL", "side": "buy", "quantity": 2})
        price_cache.update("AAPL", 210.0)

        position = client.get("/api/portfolio").json()["positions"][0]
        assert position["current_price"] == 210.0
        assert position["unrealized_pnl"] == 20.0


class TestTrade:
    def test_buy_returns_201_with_the_fill_and_the_new_portfolio(self, client):
        response = client.post(
            "/api/portfolio/trade", json={"ticker": "AAPL", "side": "buy", "quantity": 2}
        )

        assert response.status_code == 201
        body = response.json()
        assert body["trade"]["ticker"] == "AAPL"
        assert body["trade"]["side"] == "buy"
        assert body["trade"]["price"] == 200.0
        assert body["trade"]["value"] == 400.0
        assert body["portfolio"]["positions"] == [
            {
                "ticker": "AAPL",
                "quantity": 2.0,
                "avg_cost": 200.0,
                "cost_basis": 400.0,
                "current_price": 200.0,
                "market_value": 400.0,
                "unrealized_pnl": 0.0,
                "unrealized_pnl_percent": 0.0,
            }
        ]
        assert body["portfolio"]["cash_balance"] == 9600.0

    def test_sell_that_closes_the_position_reports_no_position(self, client):
        client.post("/api/portfolio/trade", json={"ticker": "AAPL", "side": "buy", "quantity": 2})
        body = client.post(
            "/api/portfolio/trade", json={"ticker": "AAPL", "side": "sell", "quantity": 2}
        ).json()

        assert body["portfolio"]["positions"] == []

    def test_accepts_a_lower_case_ticker(self, client):
        body = client.post(
            "/api/portfolio/trade", json={"ticker": "aapl", "side": "buy", "quantity": 1}
        ).json()
        assert body["trade"]["ticker"] == "AAPL"

    @pytest.mark.parametrize(
        ("payload", "detail"),
        [
            ({"ticker": "AAPL", "side": "buy", "quantity": 999}, "Insufficient cash"),
            ({"ticker": "AAPL", "side": "sell", "quantity": 1}, "no position held"),
            ({"ticker": "NFLX", "side": "buy", "quantity": 1}, "No price available"),
        ],
    )
    def test_a_rejected_trade_is_a_400_carrying_the_reason(self, client, payload, detail):
        """Not a 422: the request was well formed, the account just cannot
        support it. The message is shown to the user and fed back to the model,
        so it has to say what went wrong."""
        response = client.post("/api/portfolio/trade", json=payload)

        assert response.status_code == 400
        assert detail in response.json()["detail"]

    def test_a_rejected_trade_changes_nothing(self, client, read_cash):
        client.post("/api/portfolio/trade", json={"ticker": "AAPL", "side": "buy", "quantity": 999})

        assert read_cash() == 10000.0
        assert client.get("/api/portfolio").json()["positions"] == []

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({"ticker": "AAPL", "side": "buy", "quantity": 0}, id="zero-quantity"),
            pytest.param({"ticker": "AAPL", "side": "buy", "quantity": -5}, id="negative"),
            pytest.param({"ticker": "AAPL", "side": "buy", "quantity": "1e999"}, id="infinite"),
            pytest.param({"ticker": "AAPL", "side": "short", "quantity": 1}, id="unknown-side"),
            pytest.param({"ticker": "", "side": "buy", "quantity": 1}, id="empty-ticker"),
            pytest.param({"ticker": "../../etc", "side": "buy", "quantity": 1}, id="path-ish"),
            pytest.param({"ticker": "A" * 11, "side": "buy", "quantity": 1}, id="too-long"),
            pytest.param({"ticker": "AAPL", "side": "buy"}, id="missing-quantity"),
            pytest.param(
                {"ticker": "AAPL", "side": "buy", "quantity": 1, "price": 1},
                id="client-supplied-price",
            ),
        ],
    )
    def test_a_malformed_order_is_a_422_and_never_reaches_the_ledger(
        self, client, read_cash, payload
    ):
        """`extra="forbid"` is why the client-supplied-price case fails. A
        request naming its own fill price must not be quietly ignored — being
        ignored is indistinguishable from being honoured."""
        assert client.post("/api/portfolio/trade", json=payload).status_code == 422
        assert read_cash() == 10000.0

    def test_is_a_503_when_no_market_feed_is_running(self, sourceless_client, read_cash):
        """After a failover that could not start a replacement, every price in
        the cache is frozen at its last value. Filling against those is worse
        than refusing — the watchlist endpoints are already returning 503, so
        the account would be trading on a feed the app knows is dead."""
        response = sourceless_client.post(
            "/api/portfolio/trade", json={"ticker": "AAPL", "side": "buy", "quantity": 1}
        )

        assert response.status_code == 503
        assert read_cash() == 10000.0

    def test_reading_the_portfolio_still_works_without_a_feed(self, sourceless_client):
        """Refusing to trade is not refusing to look. The last known marks are
        still the best answer available, and a blank screen would be worse."""
        assert sourceless_client.get("/api/portfolio").status_code == 200
        assert sourceless_client.get("/api/portfolio/history").status_code == 200


class TestHistory:
    def test_starts_empty(self, client):
        assert client.get("/api/portfolio/history").json() == {"snapshots": []}

    def test_a_trade_puts_a_point_on_the_chart(self, client):
        client.post("/api/portfolio/trade", json={"ticker": "AAPL", "side": "buy", "quantity": 1})

        snapshots = client.get("/api/portfolio/history").json()["snapshots"]
        assert len(snapshots) == 1
        assert snapshots[0]["total_value"] == 10000.0
        assert snapshots[0]["recorded_at"]

    def test_points_are_oldest_first(self, client, price_cache):
        for _ in range(3):
            client.post(
                "/api/portfolio/trade", json={"ticker": "AAPL", "side": "buy", "quantity": 1}
            )
            price_cache.update("AAPL", price_cache.get_price("AAPL") + 10)

        recorded = [
            s["recorded_at"] for s in client.get("/api/portfolio/history").json()["snapshots"]
        ]
        assert recorded == sorted(recorded)

    def test_limit_returns_the_newest_points(self, client, price_cache):
        for _ in range(4):
            execute_trade(price_cache, "AAPL", "buy", 1)

        everything = client.get("/api/portfolio/history").json()["snapshots"]
        limited = client.get("/api/portfolio/history?limit=2").json()["snapshots"]
        assert limited == everything[-2:]

    @pytest.mark.parametrize("limit", [0, -1, 5001, "many"])
    def test_rejects_an_out_of_range_limit(self, client, limit):
        assert client.get(f"/api/portfolio/history?limit={limit}").status_code == 422
