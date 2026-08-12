"""Portfolio valuation and trade execution — the money rules, in one place.

This module is the *only* implementation of what a trade does. `POST
/api/portfolio/trade` calls it, and Checkpoint 4's chat handler calls the same
function for trades the LLM asks for, so a trade the model requests is
validated exactly like one the user typed. A second copy behind the chat
endpoint is the specific mistake PLAN.md §Checkpoint 4 rules out.

Rounding, decided once here rather than per call site:

* **Cash is money and rounds to cents at each fill.** The rounded figure is
  also what the position's cost basis is built from, so cash out and basis in
  always agree to the penny — computing basis from the unrounded product would
  let the two drift apart over a few hundred fractional trades.
* **Quantities are not rounded.** Fractional shares are the design (PLAN.md
  §7), and rounding a quantity would make "sell everything you hold" fail
  against its own stored value.
* **`avg_cost` is not rounded.** It is a ratio, not a price paid: rounding it
  to cents and multiplying back by a fractional quantity reintroduces exactly
  the drift the point above removes. It is rounded for display, at the edge.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from dataclasses import dataclass

from app.db import (
    DEFAULT_USER_ID,
    Position,
    Snapshot,
    Trade,
    apply_position,
    connect,
    get_cash_balance,
    get_position,
    insert_snapshot,
    insert_trade,
    list_positions,
    list_snapshots,
    read_transaction,
    set_cash_balance,
    transaction,
)
from app.market import PriceCache, PriceUpdate, normalize_ticker

logger = logging.getLogger(__name__)

BUY = "buy"
SELL = "sell"
SIDES = (BUY, SELL)

#: Share counts equal within this are the same share count. Successive
#: fractional fills leave residue in the last bits — three buys of 0.1 hold
#: 0.30000000000000004 — and without a tolerance "sell all 0.3" would be
#: rejected as an oversell, and the position row would survive at 4e-17 shares.
#: Absolute rather than relative: quantities here are bounded by $10,000 of
#: capital, so the scale where a relative epsilon would matter cannot arise.
QUANTITY_TOLERANCE = 1e-9

#: P&L history points returned by default and at most. The single owner of both
#: numbers: the API layer imports them for its query bounds, and a direct caller
#: — Checkpoint 4 assembling chat context — gets the same default rather than a
#: second one that could drift. 500 at one point per 30 s is over four hours of
#: session, more than the chart can resolve; the ceiling stops a hand-typed
#: `?limit=1000000` reading a whole table to draw a line 900 pixels wide.
DEFAULT_HISTORY_LIMIT = 500
MAX_HISTORY_LIMIT = 5000

# There is no matching tolerance for cash, and it would be dead weight if there
# were. Every cash figure and every cost is `round(…, 2)`, so each is the
# nearest double to some whole number of cents; two such values compare exactly.
# The asymmetry is the whole point — quantities carry residue because they are
# deliberately *not* rounded, and cash does not because it is.


class TradeError(ValueError):
    """A trade that must not execute: bad input, no price, or no funds/shares.

    Distinct from an unexpected failure. The API turns it into a 400 and the
    chat handler feeds its message back to the model, so the text is read by
    users and must say what went wrong and with what numbers.
    """


@dataclass(frozen=True, slots=True)
class PositionView:
    """A position marked to the current price.

    Every price-derived field is `None` when the ticker has no cached price —
    never zero. A missing price means "unknown", and rendering it as a
    100% loss would be a lie the user would act on.
    """

    ticker: str
    quantity: float
    avg_cost: float
    cost_basis: float
    current_price: float | None
    market_value: float | None
    unrealized_pnl: float | None
    unrealized_pnl_percent: float | None

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "quantity": self.quantity,
            "avg_cost": _rate(self.avg_cost),
            "cost_basis": _money(self.cost_basis),
            "current_price": self.current_price,
            "market_value": _money(self.market_value),
            "unrealized_pnl": _money(self.unrealized_pnl),
            "unrealized_pnl_percent": _rate(self.unrealized_pnl_percent),
        }


@dataclass(frozen=True, slots=True)
class PortfolioView:
    """The whole account at one instant. `GET /api/portfolio`'s response."""

    cash_balance: float
    positions: list[PositionView]
    positions_value: float
    total_value: float
    cost_basis: float
    unrealized_pnl: float
    unrealized_pnl_percent: float | None
    #: Held tickers with no cached price. Their value is excluded from the
    #: totals above, so they are named rather than dropped — a total that
    #: quietly omits a position is indistinguishable from a loss.
    unpriced_tickers: list[str]

    def to_dict(self) -> dict:
        return {
            "cash_balance": _money(self.cash_balance),
            "positions": [position.to_dict() for position in self.positions],
            "positions_value": _money(self.positions_value),
            "total_value": _money(self.total_value),
            "cost_basis": _money(self.cost_basis),
            "unrealized_pnl": _money(self.unrealized_pnl),
            "unrealized_pnl_percent": _rate(self.unrealized_pnl_percent),
            "unpriced_tickers": self.unpriced_tickers,
        }


@dataclass(frozen=True, slots=True)
class TradeResult:
    """A filled trade and the account it left behind.

    The portfolio travels with the fill so the UI needs one round trip, not two
    — and so it cannot render a position from one instant beside a cash balance
    from another.

    There is deliberately no separate `position` field. The traded position is
    already in `portfolio.positions`, in the one shape the rest of the API uses,
    and absent from it when a sell closed the holding. A second, narrower copy
    would give the frontend two places to read the same row and two rounding
    rules to keep in step.
    """

    trade: Trade
    portfolio: PortfolioView

    def to_dict(self) -> dict:
        return {
            "trade": {
                "id": self.trade.id,
                "ticker": self.trade.ticker,
                "side": self.trade.side,
                "quantity": self.trade.quantity,
                "price": self.trade.price,
                "value": _fill_value(self.trade.price, self.trade.quantity),
                "executed_at": self.trade.executed_at,
            },
            "portfolio": self.portfolio.to_dict(),
        }

    def position(self) -> PositionView | None:
        """The traded ticker's position after the fill, or None if it closed."""
        return next((p for p in self.portfolio.positions if p.ticker == self.trade.ticker), None)


# --- reads ---------------------------------------------------------------


def get_portfolio(price_cache: PriceCache, user_id: str = DEFAULT_USER_ID) -> PortfolioView:
    """Value the account against the current cache.

    Read inside a snapshot transaction, because `_value` issues two queries and
    a trade can commit between them. In autocommit that yields pre-trade cash
    beside a post-trade position — a total ten thousand dollars out, reported
    once and never repeated, which is the hardest kind of bug to be told about.
    """
    prices = price_cache.get_all()
    with read_transaction() as conn:
        return _value(conn, prices, user_id)


def get_history(
    limit: int = DEFAULT_HISTORY_LIMIT, user_id: str = DEFAULT_USER_ID
) -> list[Snapshot]:
    """Portfolio value over time, oldest first — the P&L chart's series.

    Takes no `PriceCache`, and must not: the chart plots what was recorded, not
    a curve recomputed from today's prices. Backfilling the past with the
    present would redraw history every time the market moved.
    """
    with connect() as conn:
        return list_snapshots(conn, limit=limit, user_id=user_id)


# --- writes --------------------------------------------------------------


def record_snapshot(price_cache: PriceCache, user_id: str = DEFAULT_USER_ID) -> Snapshot | None:
    """Append one point to the P&L series, or `None` if it would be a lie.

    Used by the 30-second background task. See `_record_snapshot` for when a
    point is skipped.
    """
    prices = price_cache.get_all()
    with transaction() as conn:
        return _record_snapshot(conn, _value(conn, prices, user_id), user_id)


def execute_trade(
    price_cache: PriceCache,
    ticker: str,
    side: str,
    quantity: float,
    user_id: str = DEFAULT_USER_ID,
) -> TradeResult:
    """Fill a market order at the cached price, or raise `TradeError`.

    Market orders only (PLAN.md §3): instant fill, whole quantity, no fees.

    Cash, position, blotter row and snapshot land in one transaction. Anything
    less would allow the crash that debits cash without recording the shares.

    Prices are read from the cache once, before the transaction opens, and that
    one reading fills the trade *and* values the snapshot — so the P&L point a
    trade writes is consistent with the price the trade got, rather than with
    whatever the simulator produced a few milliseconds later.
    """
    ticker = normalize_ticker(ticker)
    side = side.strip().lower()
    _validate_order(ticker, side, quantity)

    prices = price_cache.get_all()
    price = _require_price(prices, ticker, stale=price_cache.is_stale(ticker))

    with transaction() as conn:
        cash = get_cash_balance(conn, user_id)
        held = get_position(conn, ticker, user_id)

        if side == BUY:
            new_cash, new_quantity, new_avg_cost = _apply_buy(cash, held, price, quantity, ticker)
        else:
            new_cash, new_quantity, new_avg_cost = _apply_sell(cash, held, price, quantity, ticker)

        set_cash_balance(conn, new_cash, user_id)
        apply_position(conn, ticker, new_quantity, new_avg_cost, user_id)
        trade = insert_trade(conn, ticker, side, quantity, price, user_id)

        # Read back rather than construct: this is what the rest of the app
        # will see, including the `quantity == 0` delete apply_position may
        # have just performed.
        portfolio = _value(conn, prices, user_id)
        _record_snapshot(conn, portfolio, user_id)

    logger.info(
        "Trade filled: %s %s %g @ %.2f — cash %.2f", side, ticker, quantity, price, new_cash
    )
    return TradeResult(trade=trade, portfolio=portfolio)


# --- internals -----------------------------------------------------------


def _validate_order(ticker: str, side: str, quantity: float) -> None:
    """Reject what must never reach the ledger.

    Repeated in the request schema, deliberately. Pydantic guards the HTTP
    edge; this guards every edge, including the LLM's structured output in
    Checkpoint 4, which arrives as parsed floats and has never seen a form.
    """
    if not ticker:
        raise TradeError("A ticker is required.")
    if side not in SIDES:
        raise TradeError(f"Side must be 'buy' or 'sell', not {side!r}.")
    if not math.isfinite(quantity):
        # NaN fails the > 0 test below on its own, but infinity passes it and
        # would write an un-representable cash balance.
        raise TradeError("Quantity must be a finite number.")
    if quantity <= 0:
        raise TradeError(f"Quantity must be greater than zero, not {quantity:g}.")


def _require_price(prices: dict[str, PriceUpdate], ticker: str, *, stale: bool = False) -> float:
    """The fill price, or a `TradeError` explaining the wait.

    A ticker added moments ago has no price until the next poll — up to 15 s on
    Massive. Filling at 0, or at a stale price from before it was removed and
    re-added, is never the right answer (MARKET_DATA_DESIGN.md §13.2).

    `stale` closes the gap `require_live_market` cannot: that dependency catches
    a source that is *gone*, not a poller wedged while its object is still
    there. In that state every price in the cache is frozen at its last value,
    and a trade against one is a fill at a price the market has moved away
    from — the account's own record of what it paid becomes fiction. Refusing
    is the only honest answer, and it belongs here rather than in a route so
    that Checkpoint 4's chat path is covered by the same rule.
    """
    update = prices.get(ticker)
    if update is None:
        raise TradeError(f"No price available for {ticker} yet. Try again in a moment.")
    if update.price <= 0:
        raise TradeError(f"Price for {ticker} is not usable ({update.price}).")
    if stale:
        raise TradeError(
            f"The price for {ticker} has stopped updating, so it cannot be traded on. "
            "Check the market data feed and try again."
        )
    return update.price


def _apply_buy(
    cash: float, held: Position | None, price: float, quantity: float, ticker: str
) -> tuple[float, float, float]:
    """New (cash, quantity, avg_cost) after a buy."""
    cost = _fill_value(price, quantity)
    # `>`, not `>=`: a buy for exactly the cash on hand must fill, or the
    # account becomes unspendable at the one moment the user wants to spend it.
    if cost > cash:
        raise TradeError(
            f"Insufficient cash: {ticker} x{quantity:g} at ${price:,.2f} costs "
            f"${cost:,.2f}, but only ${cash:,.2f} is available."
        )

    held_quantity = held.quantity if held else 0.0
    held_basis = held.quantity * held.avg_cost if held else 0.0

    new_quantity = held_quantity + quantity
    # Weighted average of what was actually paid, using the same rounded `cost`
    # that leaves the cash balance. Sells never touch this — realised P&L is
    # the trade's business, and re-averaging on a sell would silently rewrite
    # the cost basis of the shares still held.
    new_avg_cost = (held_basis + cost) / new_quantity
    return round(cash - cost, 2), new_quantity, new_avg_cost


def _apply_sell(
    cash: float, held: Position | None, price: float, quantity: float, ticker: str
) -> tuple[float, float, float]:
    """New (cash, quantity, avg_cost) after a sell. Shorting is not supported."""
    if held is None:
        raise TradeError(f"Cannot sell {quantity:g} {ticker}: no position held.")
    if quantity > held.quantity + QUANTITY_TOLERANCE:
        raise TradeError(f"Cannot sell {quantity:g} {ticker}: only {held.quantity:g} held.")

    proceeds = _fill_value(price, quantity)
    remaining = held.quantity - quantity
    if abs(remaining) <= QUANTITY_TOLERANCE:
        remaining = 0.0  # closes the position; apply_position deletes the row

    # avg_cost is carried, never recomputed on a sell: the shares still held
    # cost what they cost, and re-averaging would rewrite their basis so that
    # selling at a loss made the remainder look more profitable.
    return round(cash + proceeds, 2), remaining, held.avg_cost


def _record_snapshot(
    conn: sqlite3.Connection, view: PortfolioView, user_id: str
) -> Snapshot | None:
    """Write one snapshot row, unless the total would be understated.

    A `portfolio_snapshots` row carries `total_value` and nothing else, so
    unlike `GET /api/portfolio` it has nowhere to say "this omits two positions
    the cache could not price". Recorded anyway, such a point is a drawdown the
    account never suffered, permanently on the P&L chart, which then "recovers"
    when the price comes back.

    A gap in the series is the honest alternative, so the point is skipped and
    the reason logged. Fully-priced portfolios — every ordinary run — are
    unaffected, since a position can only exist for a ticker that had a price.
    """
    if view.unpriced_tickers:
        logger.warning(
            "Skipping the portfolio snapshot: no price for %s",
            ", ".join(view.unpriced_tickers),
        )
        return None
    return insert_snapshot(conn, view.total_value, user_id)


def _value(conn: sqlite3.Connection, prices: dict[str, PriceUpdate], user_id: str) -> PortfolioView:
    """Mark the account to `prices`, tolerating tickers the cache does not have.

    An unpriced position contributes nothing to `positions_value`, `total_value`
    or the P&L, and is listed in `unpriced_tickers` instead
    (MARKET_DATA_DESIGN.md §13.3).
    """
    cash = get_cash_balance(conn, user_id)

    views: list[PositionView] = []
    unpriced: list[str] = []
    positions_value = 0.0
    priced_cost_basis = 0.0

    for position in list_positions(conn, user_id):
        cost_basis = position.quantity * position.avg_cost
        update = prices.get(position.ticker)

        if update is None:
            unpriced.append(position.ticker)
            views.append(
                PositionView(
                    ticker=position.ticker,
                    quantity=position.quantity,
                    avg_cost=position.avg_cost,
                    cost_basis=cost_basis,
                    current_price=None,
                    market_value=None,
                    unrealized_pnl=None,
                    unrealized_pnl_percent=None,
                )
            )
            continue

        market_value = position.quantity * update.price
        pnl = market_value - cost_basis
        positions_value += market_value
        priced_cost_basis += cost_basis
        views.append(
            PositionView(
                ticker=position.ticker,
                quantity=position.quantity,
                avg_cost=position.avg_cost,
                cost_basis=cost_basis,
                current_price=update.price,
                market_value=market_value,
                unrealized_pnl=pnl,
                unrealized_pnl_percent=(pnl / cost_basis * 100) if cost_basis else None,
            )
        )

    unrealized_pnl = positions_value - priced_cost_basis
    return PortfolioView(
        cash_balance=cash,
        positions=views,
        positions_value=positions_value,
        total_value=cash + positions_value,
        # Only the priced positions' basis, so that P&L and basis describe the
        # same set of positions and `pnl / cost_basis` stays meaningful.
        cost_basis=priced_cost_basis,
        unrealized_pnl=unrealized_pnl,
        unrealized_pnl_percent=(
            unrealized_pnl / priced_cost_basis * 100 if priced_cost_basis else None
        ),
        unpriced_tickers=unpriced,
    )


def _fill_value(price: float, quantity: float) -> float:
    """What a fill is worth, to the cent.

    The one definition. It is the amount that leaves or enters the cash balance,
    the amount a buy's cost basis is built from, and the `value` shown on the
    receipt — three things that must agree, and would not if each rounded the
    product itself.
    """
    return round(price * quantity, 2)


def _money(value: float | None) -> float | None:
    """A currency amount, rounded for display. `None` stays `None`."""
    return None if value is None else round(value, 2)


def _rate(value: float | None) -> float | None:
    """A ratio — a percentage or an average cost. Four places, because two would
    visibly mis-state the average cost of a fractional holding."""
    return None if value is None else round(value, 4)
