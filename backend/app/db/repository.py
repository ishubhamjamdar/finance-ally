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

import sqlite3
import uuid
from dataclasses import dataclass

from app.market import normalize_ticker

from .database import DEFAULT_USER_ID, utc_now


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
        delete_position(conn, ticker, user_id)
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


def delete_position(conn: sqlite3.Connection, ticker: str, user_id: str = DEFAULT_USER_ID) -> None:
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
    """Most recent trades first."""
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
    conn: sqlite3.Connection, limit: int = 500, user_id: str = DEFAULT_USER_ID
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
