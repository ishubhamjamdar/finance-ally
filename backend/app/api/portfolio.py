"""Portfolio endpoints — PLAN.md §8.

    GET  /api/portfolio          positions marked to live prices, cash, totals
    POST /api/portfolio/trade    execute a market order
    GET  /api/portfolio/history  the snapshot series behind the P&L chart

All three are `def`, not `async def`: every one of them blocks on SQLite and
none of them awaits the market source, so FastAPI runs them in a worker thread
instead of stalling the event loop that drives the simulator tick and every
open SSE stream. The watchlist handlers, which must await the source, are the
exception — see `app/api/watchlist.py`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_price_cache
from app.api.schemas import TradeRequest
from app.market import PriceCache
from app.portfolio import TradeError, execute_trade, get_history, get_portfolio

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

#: History points returned by default and at most. 500 at one point per 30 s is
#: a little over four hours of session — more than the chart can resolve — and
#: the ceiling keeps a hand-typed `?limit=1000000` from reading a whole table
#: into memory to draw a line 900 pixels wide.
DEFAULT_HISTORY_LIMIT = 500
MAX_HISTORY_LIMIT = 5000


@router.get("")
def read_portfolio(price_cache: Annotated[PriceCache, Depends(get_price_cache)]) -> dict:
    """Current positions with live marks, cash, total value and unrealised P&L.

    Positions whose ticker has no cached price keep their quantity and cost but
    report `null` marks, and are named in `unpriced_tickers`; their value is
    excluded from the totals rather than counted as zero.
    """
    return get_portfolio(price_cache).to_dict()


@router.post("/trade", status_code=status.HTTP_201_CREATED)
def create_trade(
    order: TradeRequest,
    price_cache: Annotated[PriceCache, Depends(get_price_cache)],
) -> dict:
    """Fill a market order and return the fill plus the resulting portfolio.

    400 for anything the account cannot support — unknown price, insufficient
    cash, selling more than is held. Those are ordinary outcomes of a valid
    request, so they carry the reason as `detail` rather than a 422's field
    report.

    A ticker still tracked by the market source after its position closes is
    left tracked, deliberately: the user has just been watching it, and the
    chart going flat the instant they sell out would be worse than one extra
    symbol in the poll.
    """
    try:
        result = execute_trade(price_cache, order.ticker, order.side, order.quantity)
    except TradeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return result.to_dict()


@router.get("/history")
def read_history(
    price_cache: Annotated[PriceCache, Depends(get_price_cache)],
    limit: Annotated[int, Query(ge=1, le=MAX_HISTORY_LIMIT)] = DEFAULT_HISTORY_LIMIT,
) -> dict:
    """Portfolio value over time, oldest point first.

    Truncation keeps the newest points: a chart with a gap at the left is
    readable, one missing everything since the last trade is not.
    """
    snapshots = get_history(price_cache, limit=limit)
    return {
        "snapshots": [
            {"total_value": round(snapshot.total_value, 2), "recorded_at": snapshot.recorded_at}
            for snapshot in snapshots
        ]
    }
