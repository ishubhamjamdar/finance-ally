"""The chat turn — PLAN.md §9's eight steps, in one place.

    context → prompt → model → parse → execute → persist → reply

The counterpart to `app.portfolio` and `app.watchlist`, and it obeys the same
rule they do: no `Request`, no `HTTPException`. `app/api/chat.py` maps the two
error types onto status codes and does nothing else.

**It executes nothing itself.** Every trade goes through
`app.portfolio.execute_trade` and every watchlist change through
`app.watchlist.add` / `remove` — the functions the REST endpoints call, with
the cash check, the oversell check, the atomic transaction and the size cap
already in them. PLAN.md §Checkpoint 4 rules out a second implementation behind
the chat endpoint, and the reason is worth stating plainly: a model that has
been talked into anything at all is still a client, and a client cannot be
trusted to have validated its own request.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi.concurrency import run_in_threadpool

from app.db import (
    DEFAULT_USER_ID,
    ChatMessage,
    connect,
    insert_chat_message,
    list_chat_messages,
    list_watchlist,
    read_transaction,
    transaction,
)
from app.llm import (
    MAX_HISTORY_MESSAGES,
    LLMTrade,
    LLMWatchlistChange,
    MalformedReplyError,
    RejectedAction,
    build_messages,
    complete,
    parse_reply,
)
from app.market import MarketDataSource, PriceCache, PriceUpdate, normalize_ticker
from app.portfolio import PortfolioView, TradeError, execute_trade, get_portfolio
from app.watchlist import WatchlistError
from app.watchlist import add as add_to_watchlist
from app.watchlist import remove as remove_from_watchlist

logger = logging.getLogger(__name__)

#: Shown when the model answered but the answer was unusable. Deliberately not
#: an error code: from the user's side a garbled reply is a conversational
#: failure, and the right response is to say so and invite a retry.
MALFORMED_REPLY_MESSAGE = (
    "I had trouble putting that response together. Could you rephrase, or ask me again?"
)

#: Transcript turns returned by default and at most — the single owner of both,
#: as `DEFAULT_HISTORY_LIMIT` is for the P&L series. Deliberately *not*
#: `MAX_HISTORY_MESSAGES`: how far the user can scroll back and how much
#: conversation the model is given are different questions, and a panel that
#: wanted a longer scrollback must not silently widen every prompt.
DEFAULT_TRANSCRIPT_LIMIT = 50
MAX_TRANSCRIPT_LIMIT = 500


@dataclass(frozen=True, slots=True)
class ActionResult:
    """One action the assistant attempted, and what came of it.

    Both outcomes are reported. A failed trade appearing as a visible "could
    not do that, here is why" is a PLAN.md §Checkpoint 4 exit criterion, and it
    is also what stops the transcript claiming a fill that never happened —
    the model writes its message before knowing whether the trade cleared.

    `ticker` and `action` are `None` only for an item that failed the schema
    before either could be read.
    """

    kind: str  # "trade" | "watchlist"
    ok: bool
    summary: str  # "buy 10 AAPL" — what was attempted
    detail: str  # the outcome, or the reason there wasn't one
    ticker: str | None = None
    action: str | None = None  # buy | sell | add | remove
    result: dict | None = None  # the fill or the new entry, for the UI

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "ok": self.ok,
            "summary": self.summary,
            "detail": self.detail,
            "ticker": self.ticker,
            "action": self.action,
            "result": self.result,
        }


@dataclass(frozen=True, slots=True)
class ChatReply:
    """`POST /api/chat`'s response: what was said, what was done, where that left the account."""

    message: str
    actions: list[ActionResult]
    #: Read *after* the actions, so a reply that bought something carries the
    #: balance the buy produced. The frontend updates every panel from this
    #: without a second round trip — and, more to the point, without rendering
    #: a fill beside a cash balance from before it.
    portfolio: PortfolioView

    def to_dict(self) -> dict:
        return {
            "message": self.message,
            "actions": [action.to_dict() for action in self.actions],
            "portfolio": self.portfolio.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class _Context:
    portfolio: PortfolioView
    watchlist: list[tuple[str, PriceUpdate | None]]
    history: list[ChatMessage]


async def handle_message(
    price_cache: PriceCache,
    source: MarketDataSource,
    text: str,
    user_id: str = DEFAULT_USER_ID,
) -> ChatReply:
    """Run one conversational turn.

    Raises `LLMUnavailableError` — from `app.llm.complete` — when the provider
    could not be reached at all. Nothing is written in that case: the turn did
    not happen, so history must not claim it did, and the user's message stays
    theirs to resend.

    A model that answers *badly* is the opposite case and does not raise. The
    user gets `MALFORMED_REPLY_MESSAGE`, and the exchange is recorded, because
    from the conversation's point of view it took place.
    """
    text = text.strip()
    context = await run_in_threadpool(_load_context, price_cache, user_id)

    messages = build_messages(context.portfolio, context.watchlist, context.history, text)
    raw = await run_in_threadpool(complete, messages)

    try:
        parsed = parse_reply(raw)
    except MalformedReplyError as exc:
        # `raw` is not logged: it is untrusted third-party text of unbounded
        # size, and the reason already says which way it was malformed.
        logger.warning("Discarding an unusable model reply: %s", exc)
        return await _finish(text, MALFORMED_REPLY_MESSAGE, [], price_cache, user_id)

    # Adds, then trades, then removes — because subscribing is what *creates* a
    # price and unsubscribing is what destroys one, so both have to sit on the
    # far side of the trades from each other.
    #
    # Adds first: on the simulator `add_ticker` prices a symbol immediately, so
    # "add PYPL and buy 5" fills in one turn instead of being refused for having
    # no price.
    #
    # Removes last, which is the half an earlier version got wrong. A remove
    # reconciles the ticker off the source, and `SimulatorDataSource.remove_ticker`
    # evicts it from the cache; run before the trades, "sell my AAPL and stop
    # watching it" would delete the price its own sell needed and refuse the
    # trade. Only a ticker already *held* survives the eviction, which is why
    # the bug hid — an unheld buy-then-remove is the case that breaks.
    adds = [change for change in parsed.watchlist_changes if change.action == "add"]
    removes = [change for change in parsed.watchlist_changes if change.action != "add"]

    actions = await _apply_watchlist_changes(source, adds, user_id)
    actions += await _apply_trades(price_cache, parsed.trades, user_id)
    actions += await _apply_watchlist_changes(source, removes, user_id)
    actions += [_rejection_result(rejection) for rejection in parsed.rejected]

    return await _finish(text, parsed.message, actions, price_cache, user_id)


# --- executing what the model asked for ----------------------------------


async def _apply_trades(
    price_cache: PriceCache, trades: list[LLMTrade], user_id: str
) -> list[ActionResult]:
    """Execute each trade, recording the outcome either way.

    Sequential, not gathered: each fill changes the cash balance the next one
    is checked against, and two concurrent buys would each be validated against
    the balance before the other. `execute_trade` takes the write lock, so
    parallelism here would buy contention rather than speed.

    One failure does not stop the rest. "Sell AAPL and buy MSFT" should still
    buy MSFT when the AAPL position turns out to have closed already, and the
    user is told about both.
    """
    results: list[ActionResult] = []

    for trade in trades:
        # Normalised here, not just inside `execute_trade`. The schema accepts
        # "aapl" and the domain layer upper-cases it internally, so an
        # un-normalised echo would put `ticker: "aapl"` in the reply and in
        # `chat_messages.actions` beside a fill that says "AAPL" — two spellings
        # of one symbol, and Checkpoint 7 matches actions to watchlist and
        # position rows by exactly this field.
        ticker = normalize_ticker(trade.ticker)
        summary = f"{trade.side} {trade.quantity:g} {ticker}"
        try:
            outcome = await run_in_threadpool(
                execute_trade, price_cache, ticker, trade.side, trade.quantity, user_id
            )
        except TradeError as exc:
            # An ordinary outcome — insufficient cash, no price yet, oversell.
            # Reported, never raised: the reply still has to reach the user.
            logger.info("Chat trade refused (%s): %s", summary, exc)
            results.append(
                ActionResult(
                    kind="trade",
                    ok=False,
                    summary=summary,
                    detail=str(exc),
                    ticker=ticker,
                    action=trade.side,
                )
            )
            continue

        results.append(
            ActionResult(
                kind="trade",
                ok=True,
                summary=summary,
                detail=f"Filled {trade.quantity:g} {ticker} at ${outcome.trade.price:,.2f}.",
                ticker=ticker,
                action=trade.side,
                result=outcome.to_dict()["trade"],
            )
        )

    return results


async def _apply_watchlist_changes(
    source: MarketDataSource, changes: list[LLMWatchlistChange], user_id: str
) -> list[ActionResult]:
    """Apply each watchlist change, recording the outcome either way.

    `WatchlistError` covers the whole hierarchy — already watched, not watched,
    list full, source unavailable — and every one of them is something to tell
    the user rather than a failure of the request.
    """
    results: list[ActionResult] = []

    for change in changes:
        # Normalised for the same reason as a trade's ticker — see `_apply_trades`.
        # The two branches below previously disagreed with each other: an add
        # reported `entry.ticker` (normalised) while a remove reported the raw
        # string, so the same request produced two different spellings.
        ticker = normalize_ticker(change.ticker)
        summary = f"{change.action} {ticker}"
        try:
            if change.action == "add":
                entry = await add_to_watchlist(source, ticker, user_id)
                detail = f"{ticker} added to the watchlist."
                result = {"ticker": entry.ticker, "added_at": entry.added_at}
            else:
                still_tracked = await remove_from_watchlist(source, ticker, user_id)
                detail = f"{ticker} removed from the watchlist."
                if still_tracked:
                    # Not a caveat worth hiding: the price keeps arriving, and
                    # a user who expected it to stop would otherwise think the
                    # removal failed.
                    detail += " It is still held, so it keeps streaming."
                result = {"ticker": ticker, "still_tracked": still_tracked}
        except WatchlistError as exc:
            logger.info("Chat watchlist change refused (%s): %s", summary, exc)
            results.append(
                ActionResult(
                    kind="watchlist",
                    ok=False,
                    summary=summary,
                    detail=str(exc),
                    ticker=ticker,
                    action=change.action,
                )
            )
            continue

        results.append(
            ActionResult(
                kind="watchlist",
                ok=True,
                summary=summary,
                detail=detail,
                ticker=ticker,
                action=change.action,
                result=result,
            )
        )

    return results


def _rejection_result(rejection: RejectedAction) -> ActionResult:
    """An action that failed the schema, rendered like any other failure.

    Same shape as a refused trade, so the frontend has one way to draw "the
    assistant tried to do something and it did not happen".
    """
    return ActionResult(
        kind=rejection.kind,
        ok=False,
        summary=f"unusable {rejection.kind} action: {rejection.excerpt}",
        detail=rejection.reason,
    )


# --- context and persistence ---------------------------------------------


def _load_context(price_cache: PriceCache, user_id: str) -> _Context:
    """Everything the prompt needs, read from the database and the live cache.

    Blocking SQLite; called through `run_in_threadpool`.
    """
    prices = price_cache.get_all()
    portfolio = get_portfolio(price_cache, user_id)

    # The watchlist and the history share one snapshot, per the rule in
    # backend/CLAUDE.md: two autocommit queries could straddle a watchlist
    # change and replay a history that never coexisted with that watchlist.
    #
    # The portfolio above is a *separate* read and is deliberately not folded
    # in. `get_portfolio` opens its own `read_transaction`, and reaching past it
    # to value the account against this connection would mean exporting
    # `app.portfolio`'s private valuation just so a block of advisory prose
    # could be atomic with a watchlist listing. What that costs is a mark on a
    # position and a quote on the watchlist row for the same ticker drifting by
    # one simulator tick within the rendered text. Nothing is computed from the
    # pair, and `GET /api/portfolio` — where a total is actually reported — is
    # consistent because it goes through the one transaction that matters.
    with read_transaction() as conn:
        watchlist = [
            (entry.ticker, prices.get(entry.ticker)) for entry in list_watchlist(conn, user_id)
        ]
        history = list_chat_messages(conn, limit=MAX_HISTORY_MESSAGES, user_id=user_id)

    return _Context(portfolio=portfolio, watchlist=watchlist, history=history)


async def _finish(
    user_text: str,
    reply_message: str,
    actions: list[ActionResult],
    price_cache: PriceCache,
    user_id: str,
) -> ChatReply:
    """Persist the exchange and value the account it left behind.

    A failure to write the transcript is logged and swallowed, which is the
    uncomfortable choice and still the right one. By this point the trades have
    already committed; raising would return a 500 for a request that moved real
    cash, and the obvious client response to a 500 — resend the message — would
    execute them a second time. A missing pair of transcript rows costs the user
    their scrollback for one turn. Losing the reply that says what was bought,
    while the fill sits in `trades`, costs them the ability to know it happened.
    """
    reply = ChatReply(
        message=reply_message,
        actions=actions,
        portfolio=await run_in_threadpool(get_portfolio, price_cache, user_id),
    )
    try:
        await run_in_threadpool(_persist, user_text, reply_message, actions, user_id)
    except Exception:
        logger.exception("Could not record the chat exchange; the reply is returned regardless")
    return reply


def _persist(user_text: str, reply_message: str, actions: list[ActionResult], user_id: str) -> None:
    """Write both turns in one transaction.

    Both or neither. Written separately, a crash between them leaves a question
    with no answer — or an answer with no question — replayed into the prompt
    of every later request in the session.
    """
    with transaction() as conn:
        insert_chat_message(conn, "user", user_text, None, user_id)
        insert_chat_message(
            conn,
            "assistant",
            reply_message,
            [action.to_dict() for action in actions] or None,
            user_id,
        )


def get_transcript(
    limit: int = DEFAULT_TRANSCRIPT_LIMIT, user_id: str = DEFAULT_USER_ID
) -> list[dict]:
    """The stored conversation, oldest first — `GET /api/chat/history`.

    Blocking SQLite, like the other read helpers; the handler is a `def`, so
    FastAPI already runs it in a worker thread.
    """
    with connect() as conn:
        return [
            {
                "id": message.id,
                "role": message.role,
                "content": message.content,
                "actions": message.actions,
                "created_at": message.created_at,
            }
            for message in list_chat_messages(conn, limit=limit, user_id=user_id)
        ]
