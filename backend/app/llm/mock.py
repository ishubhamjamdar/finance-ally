"""Deterministic stand-in for the model, selected by `LLM_MOCK=true`.

PLAN.md §9 asks this to serve three jobs: fast reproducible E2E tests,
development without an API key, and CI with no secrets. All three want the same
thing — the *same* answer for the same message, every time, with no network.

It returns a raw JSON string rather than a parsed object on purpose. Mock runs
then exercise `parse_reply`, `execute_trade` and `watchlist.add` exactly as
live ones do, so an E2E suite that passes here is testing the real pipeline
with only the model swapped out. A mock returning ready-made objects would skip
the parser, which is the component most likely to be wrong.

It is a pattern matcher, not a model, and reads only the newest user turn —
enough for Checkpoint 9 to drive "buy 10 AAPL" end to end and see cash move.
"""

from __future__ import annotations

import json
import re

#: "buy 10 AAPL", "sell 2.5 shares of TSLA", "BUY 3 nvda".
_TRADE_PATTERN = re.compile(
    r"\b(?P<side>buy|sell)\s+(?P<quantity>\d+(?:\.\d+)?)\s+"
    r"(?:shares?\s+(?:of\s+)?)?(?P<ticker>[A-Za-z][A-Za-z0-9.\-]{0,9})\b",
    re.IGNORECASE,
)

#: "add PYPL", "watch SQ", "remove NFLX", "unwatch V".
_WATCHLIST_PATTERN = re.compile(
    r"\b(?P<verb>add|watch|remove|unwatch)\s+"
    r"(?:the\s+ticker\s+)?(?P<ticker>[A-Za-z][A-Za-z0-9.\-]{0,9})\b",
    re.IGNORECASE,
)

_ACTION_FOR_VERB = {"add": "add", "watch": "add", "remove": "remove", "unwatch": "remove"}

#: Words that sit where a ticker sits in ordinary phrasing, and are therefore
#: not tickers: "add some cash", "watch for a dip", "buy 3 shares".
#:
#: Applied to **both** patterns, which is the fix for two ways the mock used to
#: invent symbols. "buy 3 shares" matched with `shares` as the ticker, because
#: the `shares? of` group is optional and nothing followed it. "watch for a dip
#: before you buy 2 NVDA" added `FOR` to the watchlist — and that one was not
#: cosmetic, because `SimulatorDataSource.add_ticker` invents a price for any
#: symbol at all, so the junk row then streamed for the life of the process.
#:
#: A stop list is not a parser and does not pretend to be. It is the smallest
#: thing that keeps the Checkpoint 9 E2E suite asserting on the code under test
#: rather than on this file's grammar.
_NOT_TICKERS = frozenset(
    {
        "a",
        "about",
        "after",
        "all",
        "an",
        "and",
        "any",
        "anything",
        "as",
        "at",
        "back",
        "before",
        "both",
        "but",
        "by",
        "cash",
        "closely",
        "down",
        "everything",
        "for",
        "from",
        "half",
        "her",
        "his",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "later",
        "me",
        "more",
        "most",
        "my",
        "now",
        "of",
        "off",
        "on",
        "one",
        "only",
        "or",
        "our",
        "out",
        "over",
        "please",
        "share",
        "shares",
        "so",
        "some",
        "soon",
        "thanks",
        "that",
        "the",
        "their",
        "them",
        "then",
        "these",
        "they",
        "this",
        "those",
        "to",
        "today",
        "tomorrow",
        "up",
        "us",
        "when",
        "with",
        "worth",
        "your",
    }
)


def _is_ticker(candidate: str) -> bool:
    return candidate.lower() not in _NOT_TICKERS


def mock_completion(messages: list[dict[str, str]]) -> str:
    """A canned reply to the last user message, as raw JSON."""
    user_message = _last_user_message(messages)

    trades = _extract_trades(user_message)
    changes = _extract_watchlist_changes(user_message, exclude={t["ticker"] for t in trades})

    return json.dumps(
        {
            "message": _message_for(trades, changes),
            "trades": trades,
            "watchlist_changes": changes,
        }
    )


def _last_user_message(messages: list[dict[str, str]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return message.get("content", "")
    return ""


def _extract_trades(text: str) -> list[dict]:
    return [
        {
            "ticker": match.group("ticker").upper(),
            "side": match.group("side").lower(),
            "quantity": float(match.group("quantity")),
        }
        for match in _TRADE_PATTERN.finditer(text)
        if _is_ticker(match.group("ticker"))
    ]


def _extract_watchlist_changes(text: str, exclude: set[str]) -> list[dict]:
    """Watchlist verbs in the message, minus anything already traded.

    `exclude` keeps "buy 5 AAPL and add it to my watchlist" from producing a
    trade and a stray watchlist entry for the same symbol — the buy already
    tracks it.
    """
    changes: list[dict] = []
    seen: set[str] = set()

    for match in _WATCHLIST_PATTERN.finditer(text):
        ticker = match.group("ticker").upper()
        if not _is_ticker(ticker) or ticker in exclude or ticker in seen:
            continue
        seen.add(ticker)
        changes.append({"ticker": ticker, "action": _ACTION_FOR_VERB[match.group("verb").lower()]})

    return changes


def _message_for(trades: list[dict], changes: list[dict]) -> str:
    """The conversational text. Deterministic, and it names what it did, so an
    E2E assertion can be made on the message rather than only on the actions."""
    # The verb is capitalised on its own, never the whole phrase: `str.capitalize`
    # lower-cases everything after the first character, which turned "Buying 10
    # AAPL" into "Buying 10 aapl" and made the ticker unassertable.
    parts = [
        f"{trade['side'].capitalize()}ing {trade['quantity']:g} {trade['ticker']}"
        for trade in trades
    ]
    parts += [
        f"{'Adding' if change['action'] == 'add' else 'Removing'} {change['ticker']}"
        f" {'to' if change['action'] == 'add' else 'from'} the watchlist"
        for change in changes
    ]

    if not parts:
        return (
            "Mock assistant: no live model is configured, so this is a canned reply. "
            "Ask me to buy or sell a quantity of a ticker, or to add or remove one "
            "from the watchlist, and I will carry it out."
        )
    return "Mock assistant: " + ", ".join(parts) + "."
