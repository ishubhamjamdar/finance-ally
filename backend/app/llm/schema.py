"""The structured-output contract of PLAN.md §9, and the parser for it.

    {"message": "...",
     "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 10}],
     "watchlist_changes": [{"ticker": "PYPL", "action": "add"}]}

`AssistantReply` is the schema sent to the provider as `response_format`.
Nothing parses *with* it, and that is deliberate: a reply is validated item by
item instead.

**Why not one strict `model_validate_json`.** A whole-document validation has
exactly two outcomes, so one malformed trade — a null quantity, a ticker with a
space in it, an eleventh entry past the cap — discards the model's message and
its nine good actions along with it. The user sees a generic failure and cannot
tell what the assistant meant to do. Validating each action separately keeps
the message, executes what is valid, and turns each bad item into a reportable
rejection, which is what PLAN.md §Checkpoint 4 requires of a trade that fails:
its error is returned rather than silently vanishing.

Leniency here is about *salvage*, never about trust. Every surviving item is
validated by the same `LLMTrade` / `LLMWatchlistChange` models the wire schema
is built from, and is then validated again by `app.portfolio` and
`app.watchlist`, which is where the money rules actually live. Nothing reaches
the ledger because this module was permissive.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.market import TICKER_PATTERN

#: Actions honoured from a single reply. The model is told both numbers in the
#: system prompt, so hitting one is a contract breach rather than a surprise.
#:
#: A cap is the difference between a confused model costing the user ten trades
#: and it costing them their whole balance: the chat path executes without a
#: confirmation dialog (PLAN.md §9), so nothing else stands between a repeated
#: action and the ledger. Ten is far more than any real request needs — "sell
#: everything" over a ten-ticker watchlist fits exactly.
MAX_TRADES_PER_REPLY = 10
MAX_WATCHLIST_CHANGES_PER_REPLY = 10

#: How much of an unparseable item is quoted back in its rejection. The item is
#: model output echoed into a chat transcript, so it is bounded rather than
#: pasted whole.
_REJECTION_EXCERPT_CHARS = 120


class MalformedReplyError(ValueError):
    """The reply carried no usable message, so there is nothing to salvage.

    Raised only for a total loss — not JSON, not an object, or no `message`
    string. A reply whose *actions* are malformed does not raise: those become
    rejections and the message survives.
    """


class LLMTrade(BaseModel):
    """A trade the model asked for. Field-for-field the wire schema of §9.

    The constraints repeat `TradeRequest`'s deliberately. They are not the
    authority — `app.portfolio._validate_order` is, and re-checks all of this —
    but rejecting `quantity: -5` here turns it into one reportable bad action
    instead of a `TradeError` the user reads as a failed trade.
    """

    model_config = ConfigDict(extra="forbid")

    ticker: Annotated[str, Field(pattern=TICKER_PATTERN)]
    side: Literal["buy", "sell"]
    #: `allow_inf_nan=False` is the load-bearing part: `inf > 0` is True, so
    #: `gt=0` alone would admit an infinite order size.
    quantity: Annotated[float, Field(gt=0, allow_inf_nan=False)]


class LLMWatchlistChange(BaseModel):
    """A watchlist change the model asked for."""

    model_config = ConfigDict(extra="forbid")

    ticker: Annotated[str, Field(pattern=TICKER_PATTERN)]
    action: Literal["add", "remove"]


class AssistantReply(BaseModel):
    """The `response_format` schema. Never used to parse — see the module docstring.

    `trades` and `watchlist_changes` are required rather than defaulted, even
    though §9 calls them optional: a structured-output schema with every
    property required is the form providers support most consistently, and an
    empty array costs the model two characters. The parser treats them as
    optional regardless, so a provider that omits them is handled anyway.
    """

    model_config = ConfigDict(extra="forbid")

    message: str = Field(description="The conversational response shown to the user.")
    trades: list[LLMTrade] = Field(
        description="Trades to execute now. Empty unless the user asked for or agreed to one."
    )
    watchlist_changes: list[LLMWatchlistChange] = Field(
        description="Watchlist additions and removals to apply now."
    )


@dataclass(frozen=True, slots=True)
class RejectedAction:
    """An action that never reached the domain layer, and why.

    Reported to the user rather than dropped, so "I've bought 10 AAPL" is never
    followed by silence where the trade should have been.
    """

    kind: str  # "trade" | "watchlist"
    excerpt: str  # the offending item, truncated
    reason: str


@dataclass(frozen=True, slots=True)
class ParsedReply:
    message: str
    trades: list[LLMTrade]
    watchlist_changes: list[LLMWatchlistChange]
    rejected: list[RejectedAction]


def parse_reply(raw: str | None) -> ParsedReply:
    """Salvage a `ParsedReply` from the model's raw output.

    Raises `MalformedReplyError` only when there is no message to show.
    """
    if not raw or not raw.strip():
        raise MalformedReplyError("The model returned an empty response.")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MalformedReplyError(f"The model's response was not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise MalformedReplyError(
            f"The model's response was a {type(payload).__name__}, not a JSON object."
        )

    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        raise MalformedReplyError("The model's response carried no message text.")

    trades, trade_rejections = _validate_items(
        payload.get("trades"), LLMTrade, "trade", MAX_TRADES_PER_REPLY
    )
    changes, change_rejections = _validate_items(
        payload.get("watchlist_changes"),
        LLMWatchlistChange,
        "watchlist",
        MAX_WATCHLIST_CHANGES_PER_REPLY,
    )

    return ParsedReply(
        message=message.strip(),
        trades=trades,
        watchlist_changes=changes,
        rejected=trade_rejections + change_rejections,
    )


def _validate_items(
    items: object, model: type[BaseModel], kind: str, cap: int
) -> tuple[list, list[RejectedAction]]:
    """Validate one action array, returning what survived and what did not.

    A missing array is not an error — most replies are pure conversation and
    carry none.
    """
    if items is None:
        return [], []
    if not isinstance(items, list):
        return [], [
            RejectedAction(
                kind=kind,
                excerpt=_excerpt(items),
                reason=f"Expected a list of {kind} actions, got {type(items).__name__}.",
            )
        ]

    valid: list[BaseModel] = []
    rejected: list[RejectedAction] = []
    over_cap: list[object] = []

    for item in items:
        if len(valid) >= cap:
            over_cap.append(item)
            continue
        try:
            valid.append(model.model_validate(item))
        except ValidationError as exc:
            rejected.append(RejectedAction(kind=kind, excerpt=_excerpt(item), reason=_reason(exc)))

    if over_cap:
        # Reported, not truncated silently: the model was told the cap, so
        # exceeding it is worth the user knowing about. **One** line for the
        # whole remainder — a per-item rejection would put twenty identical
        # sentences in the reply and in the stored transcript, burying whatever
        # else went wrong on that turn.
        rejected.append(
            RejectedAction(
                kind=kind,
                excerpt=_excerpt(over_cap[0])
                + (f" (and {len(over_cap) - 1} more)" if len(over_cap) > 1 else ""),
                reason=(
                    f"Ignored {len(over_cap)} {kind} action(s): a reply may carry at most "
                    f"{cap}. Ask again to run the rest."
                ),
            )
        )

    return valid, rejected


def _reason(exc: ValidationError) -> str:
    """A one-line summary of a validation failure.

    Pydantic's own rendering is multi-line and repeats the model name; this
    text is shown in a chat transcript, so it is condensed to `field: problem`.
    """
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or 'value'}: {error['msg']}"
        for error in exc.errors()
    )


def _excerpt(item: object) -> str:
    """The offending item as a short string, whatever it turned out to be."""
    try:
        text = json.dumps(item)
    except (TypeError, ValueError):  # pragma: no cover - json.loads yields only encodables
        text = repr(item)
    return text if len(text) <= _REJECTION_EXCERPT_CHARS else text[:_REJECTION_EXCERPT_CHARS] + "…"
