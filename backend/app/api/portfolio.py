"""Portfolio endpoints — PLAN.md §8.

    GET  /api/portfolio          positions marked to live prices, cash, totals
    POST /api/portfolio/trade    execute a market order
    GET  /api/portfolio/history  the snapshot series behind the P&L chart

All three are `def`, not `async def`: every one of them blocks on SQLite and
none of them awaits the market source, so FastAPI runs them in a worker thread
instead of stalling the event loop that drives the simulator tick and every
open SSE stream. The watchlist mutations, which must await the source, are the
exception — see `app/api/watchlist.py`.

The rules live in `app.portfolio`; these handlers translate between HTTP and
that module.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_price_cache, require_live_market
from app.api.schemas import TradeRequest
from app.market import PriceCache
from app.portfolio import (
    DEFAULT_HISTORY_LIMIT,
    MAX_HISTORY_LIMIT,
    TradeError,
    execute_trade,
    get_history,
    get_portfolio,
)

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.get("")
def read_portfolio(price_cache: Annotated[PriceCache, Depends(get_price_cache)]) -> dict:
    """Current positions with live marks, cash, total value and unrealised P&L.

    Positions whose ticker has no cached price keep their quantity and cost but
    report `null` marks, and are named in `unpriced_tickers`; their value is
    excluded from the totals rather than counted as zero.

    Answers even when the feed has stopped — the last known marks are the best
    available, and a blank screen would be worse. Only trading is refused.
    """
    return get_portfolio(price_cache).to_dict()


@router.post(
    "/trade",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_live_market)],
)
def create_trade(
    order: TradeRequest,
    price_cache: Annotated[PriceCache, Depends(get_price_cache)],
) -> dict:
    """Fill a market order and return the fill plus the resulting portfolio.

    400 for anything the account cannot support — unknown price, insufficient
    cash, selling more than is held. Those are ordinary outcomes of a valid
    request, so they carry the reason as `detail` rather than a 422's field
    report. 503 when no feed is running, via `require_live_market`.

    A ticker still tracked by the market source after its position closes is
    left tracked until the next watchlist change reconciles it: the user has
    just been watching it, and the chart going flat the instant they sell out
    would be worse than one extra symbol in the poll.
    """
    try:
        result = execute_trade(price_cache, order.ticker, order.side, order.quantity)
    except TradeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return result.to_dict()


@router.get("/history")
def read_history(
    limit: Annotated[int, Query(ge=1, le=MAX_HISTORY_LIMIT)] = DEFAULT_HISTORY_LIMIT,
) -> dict:
    """Portfolio value over time, oldest point first.

    Truncation keeps the newest points: a chart with a gap at the left is
    readable, one missing everything since the last trade is not.

    Takes no price cache. The series is what was recorded, never recomputed
    from today's prices.
    """
    return {
        "snapshots": [
            {"total_value": round(snapshot.total_value, 2), "recorded_at": snapshot.recorded_at}
            for snapshot in get_history(limit=limit)
        ]
    }
