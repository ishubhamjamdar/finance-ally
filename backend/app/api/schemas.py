"""Request bodies for the REST API.

Validation lives at the HTTP edge so malformed input is a 422 with a field
name, not a stack trace. It does **not** live *only* here: `app.portfolio`
re-checks the same rules, because Checkpoint 4's chat handler feeds it values
parsed out of an LLM response, which never pass through a request model.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

#: Letters, digits, dot and hyphen, starting with a letter, at most 10 —
#: covering ordinary symbols and the dotted class shares Massive uses
#: ("BRK.B"). Anything else is rejected before it can be stored, so the
#: watchlist cannot accumulate rows no data source will ever price.
TICKER_PATTERN = r"^[A-Za-z][A-Za-z0-9.\-]{0,9}$"

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
