"""Row-level reads and writes for the PLAN.md §7 tables.

Every function takes an open `sqlite3.Connection` as its first argument rather
than opening its own. That is what lets a trade — which touches `positions`,
`trades`, `users_profile` and `portfolio_snapshots` — compose four of them
inside one `transaction()` and land atomically. A repository that opened its
own connection per call could not offer that at all.

Nothing here validates. These functions write what they are given; deciding
whether a sell is larger than the position is `app.portfolio`'s job, and
duplicating the rule here would give it two homes free to disagree.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass

from app.market import normalize_ticker

from .database import DEFAULT_USER_ID, utc_now

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Position:
    """A holding. `quantity` is fractional by design (PLAN.md §7)."""

    ticker: str
    quantity: float
    avg_cost: float
    updated_at: str


@dataclass(frozen=True, slots=True)
class Trade:
    """One fill from the append-only blotter."""

    id: str
    ticker: str
    side: str  # "buy" | "sell"
    quantity: float
    price: float
    executed_at: str


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A point on the P&L chart."""

    total_value: float
    recorded_at: str


@dataclass(frozen=True, slots=True)
class WatchlistEntry:
    ticker: str
    added_at: str


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """One turn of the conversation.

    `actions` is the decoded `actions` column — what the assistant executed on
    that turn — and is `None` for user messages, which never carry any
    (PLAN.md §7).
    """

    id: str
    role: str  # "user" | "assistant"
    content: str
    actions: list[dict] | None
    created_at: str


# --- users_profile -------------------------------------------------------


def get_cash_balance(conn: sqlite3.Connection, user_id: str = DEFAULT_USER_ID) -> float:
    """The user's cash.

    Raises if the profile row is missing. That cannot happen through the
    supported path — `connect()` refuses to yield a connection whose profile
    row is absent — and inventing a default here would paper over a database
    that lazy init had failed to seed.
    """
    row = conn.execute("SELECT cash_balance FROM users_profile WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise LookupError(f"No profile row for user {user_id!r}")
    return float(row["cash_balance"])


def set_cash_balance(
    conn: sqlite3.Connection, cash_balance: float, user_id: str = DEFAULT_USER_ID
) -> None:
    conn.execute("UPDATE users_profile SET cash_balance = ? WHERE id = ?", (cash_balance, user_id))


# --- positions -----------------------------------------------------------


def list_positions(conn: sqlite3.Connection, user_id: str = DEFAULT_USER_ID) -> list[Position]:
    """Held positions, ticker order. Rows at quantity zero are not returned —
    `apply_position` deletes them, so one appearing means something wrote the
    table without going through this module."""
    rows = conn.execute(
        "SELECT ticker, quantity, avg_cost, updated_at FROM positions"
        " WHERE user_id = ? AND quantity != 0 ORDER BY ticker",
        (user_id,),
    ).fetchall()
    return [_position(row) for row in rows]


def get_position(
    conn: sqlite3.Connection, ticker: str, user_id: str = DEFAULT_USER_ID
) -> Position | None:
    row = conn.execute(
        "SELECT ticker, quantity, avg_cost, updated_at FROM positions"
        " WHERE user_id = ? AND ticker = ?",
        (user_id, normalize_ticker(ticker)),
    ).fetchone()
    return _position(row) if row is not None else None


def apply_position(
    conn: sqlite3.Connection,
    ticker: str,
    quantity: float,
    avg_cost: float,
    user_id: str = DEFAULT_USER_ID,
) -> None:
    """Write a position, or delete the row when the holding is closed.

    One function rather than upsert-plus-delete so that "quantity zero means no
    row" is enforced in a single place. A caller that forgot the delete would
    leave a 0-share row that renders as an empty line in the positions table and
    an empty tile in the heatmap.
    """
    ticker = normalize_ticker(ticker)
    if quantity == 0:
        _delete_position(conn, ticker, user_id)
        return

    conn.execute(
        """
        INSERT INTO positions (id, user_id, ticker, quantity, avg_cost, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (user_id, ticker) DO UPDATE SET
            quantity = excluded.quantity,
            avg_cost = excluded.avg_cost,
            updated_at = excluded.updated_at
        """,
        (str(uuid.uuid4()), user_id, ticker, quantity, avg_cost, utc_now()),
    )


def _delete_position(conn: sqlite3.Connection, ticker: str, user_id: str = DEFAULT_USER_ID) -> None:
    """Private on purpose: `apply_position(..., quantity=0)` is the way to close
    a holding, so "quantity zero means no row" has exactly one enforcer."""
    conn.execute(
        "DELETE FROM positions WHERE user_id = ? AND ticker = ?",
        (user_id, normalize_ticker(ticker)),
    )


def _position(row: sqlite3.Row) -> Position:
    return Position(
        ticker=row["ticker"],
        quantity=float(row["quantity"]),
        avg_cost=float(row["avg_cost"]),
        updated_at=row["updated_at"],
    )


# --- trades --------------------------------------------------------------


def insert_trade(
    conn: sqlite3.Connection,
    ticker: str,
    side: str,
    quantity: float,
    price: float,
    user_id: str = DEFAULT_USER_ID,
) -> Trade:
    trade = Trade(
        id=str(uuid.uuid4()),
        ticker=normalize_ticker(ticker),
        side=side,
        quantity=quantity,
        price=price,
        executed_at=utc_now(),
    )
    conn.execute(
        "INSERT INTO trades (id, user_id, ticker, side, quantity, price, executed_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            trade.id,
            user_id,
            trade.ticker,
            trade.side,
            trade.quantity,
            trade.price,
            trade.executed_at,
        ),
    )
    return trade


def list_trades(
    conn: sqlite3.Connection, limit: int = 100, user_id: str = DEFAULT_USER_ID
) -> list[Trade]:
    """Most recent trades first.

    No production caller yet — the blotter has no endpoint in PLAN.md §8. Kept
    because the `trades` table is otherwise write-only, and Checkpoint 6's
    positions panel or Checkpoint 7's chat context is where it surfaces. Delete
    it at Checkpoint 10 if neither claimed it.
    """
    rows = conn.execute(
        "SELECT id, ticker, side, quantity, price, executed_at FROM trades"
        " WHERE user_id = ? ORDER BY executed_at DESC, rowid DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    return [
        Trade(
            id=row["id"],
            ticker=row["ticker"],
            side=row["side"],
            quantity=float(row["quantity"]),
            price=float(row["price"]),
            executed_at=row["executed_at"],
        )
        for row in rows
    ]


# --- portfolio_snapshots -------------------------------------------------


def insert_snapshot(
    conn: sqlite3.Connection, total_value: float, user_id: str = DEFAULT_USER_ID
) -> Snapshot:
    snapshot = Snapshot(total_value=total_value, recorded_at=utc_now())
    conn.execute(
        "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at)"
        " VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), user_id, snapshot.total_value, snapshot.recorded_at),
    )
    return snapshot


def list_snapshots(
    conn: sqlite3.Connection, limit: int, user_id: str = DEFAULT_USER_ID
) -> list[Snapshot]:
    """The most recent `limit` snapshots, returned oldest first.

    A chart wants chronological order but a truncated history should keep the
    *newest* points, so the LIMIT is applied to a descending scan (which the
    `(user_id, recorded_at)` index serves directly) and the result reversed.
    `rowid` breaks ties, because two snapshots written inside the same
    microsecond — a trade landing on the background task's tick — would
    otherwise order arbitrarily.
    """
    rows = conn.execute(
        "SELECT total_value, recorded_at FROM portfolio_snapshots"
        " WHERE user_id = ? ORDER BY recorded_at DESC, rowid DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    return [
        Snapshot(total_value=float(row["total_value"]), recorded_at=row["recorded_at"])
        for row in reversed(rows)
    ]


# --- watchlist -----------------------------------------------------------


def list_watchlist(
    conn: sqlite3.Connection, user_id: str = DEFAULT_USER_ID
) -> list[WatchlistEntry]:
    """Watchlist in the order tickers were added, so the grid does not reshuffle
    when a price changes."""
    rows = conn.execute(
        "SELECT ticker, added_at FROM watchlist WHERE user_id = ? ORDER BY added_at, rowid",
        (user_id,),
    ).fetchall()
    return [WatchlistEntry(ticker=row["ticker"], added_at=row["added_at"]) for row in rows]


def count_watchlist(conn: sqlite3.Connection, user_id: str = DEFAULT_USER_ID) -> int:
    """How many tickers are watched. Cheaper than `len(list_watchlist(...))`,
    and the size cap in `app.watchlist` checks it on every add."""
    return conn.execute("SELECT COUNT(*) FROM watchlist WHERE user_id = ?", (user_id,)).fetchone()[
        0
    ]


def add_watchlist_entry(
    conn: sqlite3.Connection, ticker: str, user_id: str = DEFAULT_USER_ID
) -> WatchlistEntry | None:
    """Add a ticker. Returns the new entry, or None if it was already there.

    The UNIQUE (user_id, ticker) constraint decides, not a prior SELECT: two
    concurrent adds would both pass the SELECT and one would then raise.
    """
    entry = WatchlistEntry(ticker=normalize_ticker(ticker), added_at=utc_now())
    cursor = conn.execute(
        "INSERT OR IGNORE INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), user_id, entry.ticker, entry.added_at),
    )
    return entry if cursor.rowcount > 0 else None


def delete_watchlist_entry(
    conn: sqlite3.Connection, ticker: str, user_id: str = DEFAULT_USER_ID
) -> bool:
    """Remove a ticker. Returns False if it was not on the watchlist."""
    cursor = conn.execute(
        "DELETE FROM watchlist WHERE user_id = ? AND ticker = ?",
        (user_id, normalize_ticker(ticker)),
    )
    return cursor.rowcount > 0


# --- chat_messages -------------------------------------------------------


def insert_chat_message(
    conn: sqlite3.Connection,
    role: str,
    content: str,
    actions: list[dict] | None = None,
    user_id: str = DEFAULT_USER_ID,
) -> ChatMessage:
    """Append one turn to the conversation.

    `actions` is stored as JSON because PLAN.md §7 defines the column that way.
    Encoding here rather than at the call site means the write and the read
    below share one format by construction; a caller that passed a pre-encoded
    string would be choosing an encoding the reader has to guess at.
    """
    message = ChatMessage(
        id=str(uuid.uuid4()),
        role=role,
        content=content,
        actions=actions,
        created_at=utc_now(),
    )
    conn.execute(
        "INSERT INTO chat_messages (id, user_id, role, content, actions, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            message.id,
            user_id,
            message.role,
            message.content,
            json.dumps(actions) if actions else None,
            message.created_at,
        ),
    )
    return message


def list_chat_messages(
    conn: sqlite3.Connection, limit: int, user_id: str = DEFAULT_USER_ID
) -> list[ChatMessage]:
    """The most recent `limit` turns, returned oldest first.

    Same shape as `list_snapshots` and for the same reason: a conversation is
    replayed in order, but a truncated one must keep the *newest* turns, so the
    LIMIT is applied to a descending scan — which the `(user_id, created_at)`
    index serves directly — and the result reversed. `rowid` breaks ties,
    because the user message and the assistant's reply to it are written inside
    one transaction and can share a timestamp to the microsecond; ordered
    arbitrarily they would replay as the answer preceding the question.
    """
    rows = conn.execute(
        "SELECT id, role, content, actions, created_at FROM chat_messages"
        " WHERE user_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    return [_chat_message(row) for row in reversed(rows)]


def _chat_message(row: sqlite3.Row) -> ChatMessage:
    return ChatMessage(
        id=row["id"],
        role=row["role"],
        content=row["content"],
        actions=_decode_actions(row["actions"], row["id"]),
        created_at=row["created_at"],
    )


def _decode_actions(raw: str | None, message_id: str) -> list[dict] | None:
    """Decode the `actions` column, tolerating a row that cannot be decoded.

    The only free-form JSON in the schema, and the only column whose contents
    this module cannot itself guarantee — a hand-edited database is enough. A
    raised `JSONDecodeError` here would not cost one message: history replay
    reads that row on *every* subsequent chat request, so one bad row would
    make the endpoint permanently unusable. Dropping the actions costs an
    inline confirmation the user already saw.
    """
    if raw is None:
        return None
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Chat message %s has undecodable actions; ignoring them", message_id)
        return None
    return decoded if isinstance(decoded, list) else None
