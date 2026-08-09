"""Tests for `app.portfolio` — valuation and the money rules.

These run against the domain functions rather than the HTTP layer on purpose.
Checkpoint 4's chat handler calls exactly these functions, so what is asserted
here is what protects a trade the LLM asks for, not just one typed into a form.
"""

from __future__ import annotations

import math

import pytest

from app.db import DEFAULT_USER_ID, connect, get_position, list_positions, list_trades
from app.market import PriceCache
from app.portfolio import (
    TradeError,
    execute_trade,
    get_history,
    get_portfolio,
    record_snapshot,
)


class DriftingPriceCache(PriceCache):
    """A cache whose price moves the moment it is read a second time.

    Stands in for the simulator ticking mid-trade. A static cache cannot tell
    a trade that reads prices once from one that reads them twice, and the
    difference between those is a snapshot that books a gain out of nothing.
    """

    def __init__(self, ticker: str, drifts_to: float) -> None:
        super().__init__()
        self._ticker = ticker
        self._drifts_to = drifts_to
        self._reads = 0

    def get_all(self):
        self._reads += 1
        if self._reads > 1:
            self.update(self._ticker, self._drifts_to)
        return super().get_all()


def snapshot_count() -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM portfolio_snapshots").fetchone()[0]


def position(ticker: str):
    with connect() as conn:
        return get_position(conn, ticker)


class TestBuy:
    def test_debits_cash_and_opens_a_position(self, price_cache, read_cash):
        result = execute_trade(price_cache, "AAPL", "buy", 10)

        assert read_cash() == 8000.0
        assert result.position.quantity == 10
        assert result.position.avg_cost == 200.0
        assert result.trade.price == 200.0
        assert result.trade.side == "buy"

    def test_rejects_a_buy_the_cash_balance_cannot_cover(self, price_cache, read_cash):
        with pytest.raises(TradeError, match="Insufficient cash"):
            execute_trade(price_cache, "AAPL", "buy", 100)  # $20,000 against $10,000

        assert read_cash() == 10000.0
        assert position("AAPL") is None

    def test_allows_a_buy_that_spends_the_balance_exactly(self, price_cache, read_cash):
        """The boundary is `cost > cash`, not `>=`. Rejecting a buy for exactly
        the cash on hand would make the account permanently unspendable at the
        one moment the user most wants to spend it."""
        execute_trade(price_cache, "AAPL", "buy", 50)  # 50 x $200 = $10,000
        assert read_cash() == 0.0

    def test_averages_cost_across_successive_buys(self, price_cache):
        """Weighted by quantity, not a mean of the two prices — 5 shares at
        $200 then 15 at $300 averages $275, never $250."""
        execute_trade(price_cache, "AAPL", "buy", 5)
        price_cache.update("AAPL", 300.0)
        execute_trade(price_cache, "AAPL", "buy", 15)

        held = position("AAPL")
        assert held.quantity == 20
        assert held.avg_cost == pytest.approx(275.0)

    def test_cost_basis_matches_the_cash_actually_spent(self, price_cache, read_cash):
        """Basis in and cash out are computed from the same rounded figure, so
        after any number of odd-priced fractional fills the two still agree to
        the penny."""
        price_cache.update("AAPL", 33.33)
        for _ in range(20):
            execute_trade(price_cache, "AAPL", "buy", 0.37)

        held = position("AAPL")
        spent = round(10000.0 - read_cash(), 2)
        assert round(held.quantity * held.avg_cost, 2) == spent


class TestSell:
    def test_credits_cash_and_reduces_the_position(self, price_cache, read_cash):
        execute_trade(price_cache, "AAPL", "buy", 10)
        price_cache.update("AAPL", 250.0)
        execute_trade(price_cache, "AAPL", "sell", 4)

        assert read_cash() == 8000.0 + 1000.0
        assert position("AAPL").quantity == 6

    def test_rejects_selling_more_than_is_held(self, price_cache, read_cash):
        execute_trade(price_cache, "AAPL", "buy", 5)
        cash_after_buy = read_cash()

        with pytest.raises(TradeError, match="only 5 held"):
            execute_trade(price_cache, "AAPL", "sell", 5.5)

        assert read_cash() == cash_after_buy
        assert position("AAPL").quantity == 5

    def test_rejects_selling_a_ticker_that_is_not_held(self, price_cache):
        with pytest.raises(TradeError, match="no position held"):
            execute_trade(price_cache, "GOOGL", "sell", 1)

    def test_leaves_average_cost_untouched(self, price_cache):
        """A sell realises P&L; it does not re-price the shares still held.
        Recomputing the average here is how selling at a loss would make the
        remainder look more profitable than it is."""
        execute_trade(price_cache, "AAPL", "buy", 10)
        price_cache.update("AAPL", 50.0)  # sell well below cost
        execute_trade(price_cache, "AAPL", "sell", 4)

        assert position("AAPL").avg_cost == 200.0

    def test_removes_the_row_when_the_holding_closes(self, price_cache):
        execute_trade(price_cache, "AAPL", "buy", 10)
        result = execute_trade(price_cache, "AAPL", "sell", 10)

        assert position("AAPL") is None
        assert result.position is None
        with connect() as conn:
            assert list_positions(conn) == []

    def test_selling_at_a_loss_still_credits_the_lower_price(self, price_cache, read_cash):
        execute_trade(price_cache, "AAPL", "buy", 10)  # -$2,000
        price_cache.update("AAPL", 120.0)
        execute_trade(price_cache, "AAPL", "sell", 10)  # +$1,200

        assert read_cash() == 9200.0


class TestFractionalShares:
    def test_fills_a_fractional_quantity(self, price_cache, read_cash):
        execute_trade(price_cache, "AAPL", "buy", 0.25)

        assert read_cash() == 9950.0
        assert position("AAPL").quantity == 0.25

    def test_selling_the_whole_fractional_holding_closes_it(self, price_cache):
        """Three buys of 0.1 hold 0.30000000000000004, not 0.3. Without a
        tolerance the user could never sell the position they can see, and the
        row would survive at 4e-17 shares — a ghost line in the positions table
        and a tile in the heatmap sized at nothing."""
        for _ in range(3):
            execute_trade(price_cache, "AAPL", "buy", 0.1)
        assert position("AAPL").quantity != 0.3  # the residue is real

        execute_trade(price_cache, "AAPL", "sell", 0.3)
        assert position("AAPL") is None

    def test_rounds_each_fill_to_the_cent(self, price_cache, read_cash):
        price_cache.update("AAPL", 190.37)
        execute_trade(price_cache, "AAPL", "buy", 1.0 / 3.0)  # $63.4566…

        assert read_cash() == round(10000.0 - 63.46, 2)


class TestOrderValidation:
    @pytest.mark.parametrize("quantity", [0, 0.0, -1, -0.001])
    def test_rejects_non_positive_quantities(self, price_cache, quantity):
        with pytest.raises(TradeError, match="greater than zero"):
            execute_trade(price_cache, "AAPL", "buy", quantity)

    @pytest.mark.parametrize("quantity", [math.inf, -math.inf, math.nan])
    def test_rejects_quantities_that_are_not_finite(self, price_cache, quantity):
        """`inf > 0` is True, so the positivity check alone lets an infinite
        order through and writes an un-representable cash balance."""
        with pytest.raises(TradeError):
            execute_trade(price_cache, "AAPL", "buy", quantity)

    def test_rejects_an_unknown_side(self, price_cache):
        with pytest.raises(TradeError, match="buy.*sell"):
            execute_trade(price_cache, "AAPL", "short", 1)

    def test_rejects_an_empty_ticker(self, price_cache):
        with pytest.raises(TradeError, match="ticker is required"):
            execute_trade(price_cache, "   ", "buy", 1)

    def test_rejects_a_ticker_with_no_cached_price(self, price_cache):
        """A just-added ticker has no price for up to one poll interval.
        Filling at zero would hand the user free shares."""
        with pytest.raises(TradeError, match="No price available for PYPL"):
            execute_trade(price_cache, "PYPL", "buy", 1)

    def test_rejects_a_non_positive_cached_price(self, price_cache):
        price_cache.update("AAPL", 0.0)
        with pytest.raises(TradeError, match="not usable"):
            execute_trade(price_cache, "AAPL", "buy", 1)

    @pytest.mark.parametrize("side", ["BUY", " Buy "])
    def test_accepts_a_side_in_any_case(self, price_cache, side):
        assert execute_trade(price_cache, "AAPL", side, 1).trade.side == "buy"

    def test_normalises_the_ticker(self, price_cache):
        """A lower-case row is a row the market source never prices."""
        execute_trade(price_cache, " aapl ", "buy", 1)
        assert position("AAPL").quantity == 1


class TestTransactionality:
    def test_a_failure_part_way_through_leaves_nothing_behind(
        self, price_cache, read_cash, monkeypatch
    ):
        """Cash, position, blotter and snapshot are one transaction. A trade
        that debits the cash without recording the shares is the worst outcome
        this endpoint has, and it is exactly what a per-statement commit gives."""

        def explode(*args, **kwargs):
            raise RuntimeError("disk I/O error")

        monkeypatch.setattr("app.portfolio.insert_trade", explode)

        with pytest.raises(RuntimeError):
            execute_trade(price_cache, "AAPL", "buy", 10)

        assert read_cash() == 10000.0
        assert position("AAPL") is None
        assert snapshot_count() == 0

    def test_records_the_fill_in_the_blotter(self, price_cache):
        execute_trade(price_cache, "AAPL", "buy", 2)
        execute_trade(price_cache, "AAPL", "sell", 1)

        with connect() as conn:
            trades = list_trades(conn)
        assert [(t.side, t.quantity, t.price) for t in trades] == [
            ("sell", 1.0, 200.0),
            ("buy", 2.0, 200.0),
        ]


class TestSnapshots:
    def test_a_trade_writes_a_snapshot_immediately(self, price_cache):
        """Checkpoint 3 exit criterion: the P&L chart has a point at the trade
        time, rather than waiting up to 30 s for the background task."""
        assert snapshot_count() == 0
        execute_trade(price_cache, "AAPL", "buy", 10)
        assert snapshot_count() == 1

    def test_the_trades_snapshot_is_valued_at_the_fill_price(self, price_cache):
        """A market order moves value between cash and shares and destroys
        none of it, so the point written by the trade must equal the total
        before it."""
        execute_trade(price_cache, "AAPL", "buy", 10)

        history = get_history(price_cache)
        assert [snapshot.total_value for snapshot in history] == [10000.0]

    def test_the_fill_and_the_snapshot_read_the_same_prices(self):
        """The property above only holds if the whole trade works from one
        reading of the cache. Against a cache that moves between reads — which
        the simulator does, twice a second — a snapshot that re-read prices
        would book an instant gain the user never made.
        """
        cache = DriftingPriceCache("AAPL", drifts_to=400.0)
        cache.update("AAPL", 200.0)

        execute_trade(cache, "AAPL", "buy", 10)  # $2,000 of the $10,000

        assert [s.total_value for s in get_history(cache)] == [10000.0]

    def test_the_background_task_appends_a_point(self, price_cache):
        record_snapshot(price_cache)
        record_snapshot(price_cache)
        assert [s.total_value for s in get_history(price_cache)] == [10000.0, 10000.0]

    def test_history_is_oldest_first_and_keeps_the_newest_when_truncated(self, price_cache):
        for _ in range(5):
            execute_trade(price_cache, "AAPL", "buy", 1)
            price_cache.update("AAPL", price_cache.get_price("AAPL") + 100)

        recorded = [s.recorded_at for s in get_history(price_cache)]
        assert recorded == sorted(recorded)

        newest_two = get_history(price_cache, limit=2)
        assert [s.recorded_at for s in newest_two] == recorded[-2:]


class TestValuation:
    def test_an_untouched_account_is_all_cash(self, price_cache):
        view = get_portfolio(price_cache)

        assert view.cash_balance == 10000.0
        assert view.positions == []
        assert view.total_value == 10000.0
        assert view.unrealized_pnl == 0.0
        assert view.unrealized_pnl_percent is None  # no basis to divide by

    def test_marks_positions_to_the_current_price(self, price_cache):
        execute_trade(price_cache, "AAPL", "buy", 10)
        price_cache.update("AAPL", 260.0)

        view = get_portfolio(price_cache)
        held = view.positions[0]

        assert held.current_price == 260.0
        assert held.market_value == 2600.0
        assert held.cost_basis == 2000.0
        assert held.unrealized_pnl == 600.0
        assert held.unrealized_pnl_percent == pytest.approx(30.0)
        assert view.total_value == 8000.0 + 2600.0

    def test_excludes_unpriced_positions_from_the_total_and_names_them(
        self, price_cache, add_position
    ):
        """MARKET_DATA_DESIGN.md §13.3. A held ticker the cache has lost must
        not be marked at zero — that reads as a total loss the user would act
        on — nor be dropped silently, which is indistinguishable from one."""
        add_position("PYPL", quantity=5)  # never priced by this cache

        view = get_portfolio(price_cache)
        pypl = next(p for p in view.positions if p.ticker == "PYPL")

        assert pypl.quantity == 5
        assert pypl.current_price is None
        assert pypl.market_value is None
        assert pypl.unrealized_pnl is None
        assert view.unpriced_tickers == ["PYPL"]
        assert view.total_value == 10000.0  # cash only
        assert view.cost_basis == 0.0  # the priced set is empty

    def test_aggregate_pnl_covers_every_priced_position(self, price_cache):
        execute_trade(price_cache, "AAPL", "buy", 10)  # $2,000 basis
        execute_trade(price_cache, "GOOGL", "buy", 10)  # $1,000 basis
        price_cache.update("AAPL", 220.0)  # +$200
        price_cache.update("GOOGL", 90.0)  # -$100

        view = get_portfolio(price_cache)

        assert view.cost_basis == 3000.0
        assert view.unrealized_pnl == pytest.approx(100.0)
        assert view.unrealized_pnl_percent == pytest.approx(100 / 3000 * 100)
        assert view.positions_value == pytest.approx(3100.0)

    def test_serialises_to_the_documented_shape(self, price_cache):
        execute_trade(price_cache, "AAPL", "buy", 1)
        body = get_portfolio(price_cache).to_dict()

        assert set(body) == {
            "cash_balance",
            "positions",
            "positions_value",
            "total_value",
            "cost_basis",
            "unrealized_pnl",
            "unrealized_pnl_percent",
            "unpriced_tickers",
        }
        assert set(body["positions"][0]) == {
            "ticker",
            "quantity",
            "avg_cost",
            "cost_basis",
            "current_price",
            "market_value",
            "unrealized_pnl",
            "unrealized_pnl_percent",
        }


class TestUserScoping:
    def test_another_user_id_has_its_own_ledger(self, price_cache, read_cash):
        """The user_id column is threaded through every query rather than
        assumed, so PLAN.md §7's multi-user path needs no migration and no
        rewrite of this module."""
        with connect() as conn:
            conn.execute(
                "INSERT INTO users_profile (id, cash_balance, created_at) VALUES (?, ?, ?)",
                ("other", 500.0, "2026-01-01T00:00:00+00:00"),
            )

        execute_trade(price_cache, "AAPL", "buy", 1, user_id="other")

        assert read_cash("other") == 300.0
        assert read_cash(DEFAULT_USER_ID) == 10000.0
        assert get_portfolio(price_cache, user_id=DEFAULT_USER_ID).positions == []
