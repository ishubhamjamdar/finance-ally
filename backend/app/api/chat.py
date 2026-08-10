"""Chat endpoints — PLAN.md §8, §9.

    POST /api/chat          send a message, get the reply and what it executed
    GET  /api/chat/history  the stored transcript, for reload

The rules live in `app.chat`; this module maps its two failure modes onto
status codes and does nothing else.

`POST` is `async def` because the turn awaits both the provider and the market
source. `GET` is a plain `def` — one blocking query, which FastAPI runs in a
worker thread.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_market_source, get_price_cache
from app.api.schemas import ChatRequest
from app.chat import (
    DEFAULT_TRANSCRIPT_LIMIT,
    MAX_TRANSCRIPT_LIMIT,
    get_transcript,
    handle_message,
)
from app.llm import LLMUnavailableError
from app.market import MarketDataSource, PriceCache

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("")
async def create_chat_message(
    payload: ChatRequest,
    price_cache: Annotated[PriceCache, Depends(get_price_cache)],
    source: Annotated[MarketDataSource, Depends(get_market_source)],
) -> dict:
    """Send one message and get the complete reply (PLAN.md §9 — no streaming).

    Takes the market source because the turn may execute trades and watchlist
    changes, so it inherits the 503 `get_market_source` raises when no feed is
    running. That is the same policy `POST /api/portfolio/trade` takes through
    `require_live_market`, and for the same reason: with a dead feed every
    price is frozen at its last value, and a chat that kept filling against
    them would be a way around the refusal the trade bar gives. It costs the
    ability to ask a question while the feed is down, which is the right trade
    — the answer would be built on frozen numbers presented as current.

    503 also when the provider cannot be reached. Nothing is persisted then, so
    resending the same message is safe and is the correct thing to do.

    A model that *replies* badly is not an error here: it produces a 200 whose
    message says so. Malformed output must never surface as a 500 (PLAN.md
    §Checkpoint 4), and a garbled answer is a conversational outcome, not a
    broken request.
    """
    try:
        reply = await handle_message(price_cache, source, payload.message)
    except LLMUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return reply.to_dict()


@router.get("/history")
def read_chat_history(
    limit: Annotated[int, Query(ge=1, le=MAX_TRANSCRIPT_LIMIT)] = DEFAULT_TRANSCRIPT_LIMIT,
) -> dict:
    """The conversation so far, oldest turn first.

    Truncation keeps the newest turns, as the P&L history does: a transcript
    missing its beginning is readable, one missing everything since the last
    trade is not.
    """
    return {"messages": get_transcript(limit=limit)}
