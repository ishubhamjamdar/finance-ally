"""Request bodies for the REST API.

Validation lives at the HTTP edge so malformed input is a 422 with a field
name, not a stack trace. It does **not** live *only* here: `app.portfolio`
re-checks the same rules, because Checkpoint 4's chat handler feeds it values
parsed out of an LLM response, which never pass through a request model.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.market import TICKER_PATTERN

# TICKER_PATTERN is defined in app.market, beside normalize_ticker: three
# layers now validate symbols against it, and the rule belongs to the one that
# knows what a symbol is. Rejecting anything else here is still what stops the
# watchlist accumulating rows no data source will ever price.

Ticker = Annotated[
    str,
    Field(pattern=TICKER_PATTERN, description="Ticker symbol, e.g. AAPL. Case-insensitive."),
]


class TradeRequest(BaseModel):
    """`POST /api/portfolio/trade` — a market order (PLAN.md §8)."""

    model_config = ConfigDict(extra="forbid")

    ticker: Ticker
    side: Literal["buy", "sell"]
    #: `allow_inf_nan=False` is the part that matters: `inf > 0` is True, so
    #: `gt=0` alone would admit an infinite order size.
    quantity: float = Field(gt=0, allow_inf_nan=False, description="Shares; may be fractional.")


class WatchlistAddRequest(BaseModel):
    """`POST /api/watchlist`."""

    model_config = ConfigDict(extra="forbid")

    ticker: Ticker


#: The longest chat message accepted. This is the one field in the API filled
#: entirely with free text, and every character of it is forwarded to a paid
#: provider inside a prompt that also carries the portfolio and twenty turns of
#: history — so it is bounded at the edge rather than discovered at the context
#: window. 2,000 characters is several paragraphs; no real question is longer.
MAX_CHAT_MESSAGE_CHARS = 2000


class ChatRequest(BaseModel):
    """`POST /api/chat` — one turn of conversation (PLAN.md §8)."""

    model_config = ConfigDict(extra="forbid")

    #: Stripped before the length checks, so a message of nothing but spaces is
    #: a 422 rather than an empty prompt sent to the model at full price.
    message: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_CHAT_MESSAGE_CHARS),
        Field(description="What to say to the assistant."),
    ]
