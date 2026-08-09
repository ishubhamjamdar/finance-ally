"""SQLite connection handling and lazy initialisation.

PLAN.md §7: there is no migration step and no setup command. The first
connection that finds the tables missing creates and seeds them, so a fresh
Docker volume, a deleted database file, and a first run on a developer laptop
all behave identically.

Threading model: one connection per operation, closed when the operation ends.
FastAPI runs `def` handlers in a worker thread, so a shared connection would
need `check_same_thread=False` plus external serialisation; opening a fresh one
costs microseconds against a local file and sidesteps the whole question.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.market import DEFAULT_TICKERS, normalize_ticker

logger = logging.getLogger(__name__)

DEFAULT_USER_ID = "default"
STARTING_CASH = 10000.0

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

#: Repo-root `db/finally.db` — the directory PLAN.md §11 volume-mounts to
#: /app/db in the container. In the image the package does not sit under the
#: repo root, so the Dockerfile sets DB_PATH explicitly.
_DEFAULT_DB_PATH = Path(__file__).resolve().parents[3] / "db" / "finally.db"

#: The tables `_is_initialized` looks for. Checked per connection rather than
#: cached in a module flag: a flag would make the process believe a database it
#: created still exists, and serve `no such table` for the rest of its life
#: once the file was deleted underneath it.
REQUIRED_TABLES = (
    "users_profile",
    "watchlist",
    "positions",
    "trades",
    "portfolio_snapshots",
    "chat_messages",
)

# Serialises initialisation between threads of this process. Cross-process
# safety comes from BEGIN IMMEDIATE plus the IF NOT EXISTS / INSERT OR IGNORE
# statements in schema.sql and _seed().
_init_lock = threading.Lock()


def utc_now() -> str:
    """ISO-8601 UTC timestamp — the format every `*_at` column stores."""
    return datetime.now(timezone.utc).isoformat()


def get_db_path() -> Path:
    """Resolve the database file, reading DB_PATH on every call.

    Not captured at import: tests point it at a tmp directory, and reading it
    late means they need no module reload to do so.
    """
    raw = os.environ.get("DB_PATH", "").strip()
    return Path(raw) if raw else _DEFAULT_DB_PATH


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Open an initialised connection, closing it on exit.

    Autocommit: each statement commits on its own. Group writes that must
    land together with `transaction()`.
    """
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path, timeout=10.0, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        # WAL lets the SSE stream and snapshot writer read while a trade
        # writes. busy_timeout covers the writer-vs-writer case that WAL does
        # not: block for up to 5 s rather than raising "database is locked".
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        ensure_initialized(conn)
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """A connection wrapped in an exclusive write transaction.

    BEGIN IMMEDIATE takes the write lock up front, so two concurrent writers
    queue on busy_timeout instead of one discovering at COMMIT that it has to
    be rolled back.
    """
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.rollback()
            raise
        conn.commit()


def ensure_initialized(conn: sqlite3.Connection) -> None:
    """Create and seed the schema if the database is not usable yet.

    Double-checked under a lock so that N threads racing on the first request
    of a cold start run the schema once, and the N-1 that lose the race do not
    return before it finishes.

    Schema and seed land in one transaction, so a crash or an I/O error part
    way through leaves nothing behind to be mistaken for a working database.
    """
    if _is_initialized(conn):
        return

    with _init_lock:
        if _is_initialized(conn):
            return
        logger.info("Initialising database at %s", get_db_path())
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in _schema_statements():
                conn.execute(statement)
            _seed(conn)
        except BaseException:
            conn.rollback()
            raise
        conn.commit()
        logger.info(
            "Database initialised: %d tables, %d seed tickers",
            len(REQUIRED_TABLES),
            len(DEFAULT_TICKERS),
        )


def _schema_statements() -> list[str]:
    """schema.sql split into individual statements.

    Deliberately not `executescript()`. That method issues an implicit COMMIT
    before it runs, which ends the BEGIN IMMEDIATE above and leaves each CREATE
    TABLE committing on its own — so a seed that then failed would leave six
    empty tables behind, permanently, because the presence of those tables is
    what tells the next connection there is nothing to do. `rollback()` in that
    world rolls back nothing at all.

    `complete_statement` is quote- and comment-aware, unlike splitting on ";".
    """
    statements: list[str] = []
    buffer = ""
    for line in _SCHEMA_PATH.read_text().splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statements.append(buffer)
            buffer = ""
    return statements


def _is_initialized(conn: sqlite3.Connection) -> bool:
    """Whether the database is both structured and seeded.

    The seed row is part of the test, not just the tables. A database with all
    six tables and no profile row cannot serve a single request — checking only
    for tables is what would let a half-built one be treated as finished, and
    the app would run forever with no cash balance.

    Only the profile row is required, not the watchlist: removing every ticker
    is something a user may legitimately do, and must not trigger a reseed.
    """
    if not _tables_present(conn):
        return False
    row = conn.execute("SELECT 1 FROM users_profile WHERE id = ?", (DEFAULT_USER_ID,)).fetchone()
    return row is not None


def _tables_present(conn: sqlite3.Connection) -> bool:
    placeholders = ",".join("?" * len(REQUIRED_TABLES))
    row = conn.execute(
        f"SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN ({placeholders})",
        REQUIRED_TABLES,
    ).fetchone()
    return row[0] == len(REQUIRED_TABLES)


def _seed(conn: sqlite3.Connection) -> None:
    """Insert the PLAN.md §7 default rows: one profile, ten watchlist tickers.

    INSERT OR IGNORE throughout, so seeding a database another process seeded
    a millisecond earlier is a no-op rather than a constraint violation.

    Reached only when `_is_initialized` says no — a missing table or a missing
    profile row. Deleting every watchlist entry is not one of those conditions,
    so a ticker the user removed stays removed.
    """
    now = utc_now()
    conn.execute(
        "INSERT OR IGNORE INTO users_profile (id, cash_balance, created_at) VALUES (?, ?, ?)",
        (DEFAULT_USER_ID, STARTING_CASH, now),
    )
    conn.executemany(
        "INSERT OR IGNORE INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
        [(str(uuid.uuid4()), DEFAULT_USER_ID, ticker, now) for ticker in DEFAULT_TICKERS],
    )


def load_tracked_tickers(user_id: str = DEFAULT_USER_ID) -> list[str]:
    """Every ticker the market data source must stream: watchlist ∪ positions.

    Positions are in the union deliberately. A ticker dropped from the
    watchlist while still held would otherwise stop being priced, and the
    portfolio would silently lose that position's value from its total
    (MARKET_DATA_DESIGN.md §13.3–13.4).
    """
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT ticker FROM watchlist WHERE user_id = ?
            UNION
            SELECT ticker FROM positions WHERE user_id = ? AND quantity != 0
            """,
            (user_id, user_id),
        ).fetchall()

    # Deduplicated AFTER normalising, not by the UNION. UNION compares the raw
    # strings and the UNIQUE (user_id, ticker) constraint is case-sensitive, so
    # 'aapl' and 'AAPL' both survive the query and both normalise to AAPL —
    # which would price the same ticker twice and bloat every Massive request.
    return sorted({normalize_ticker(row["ticker"]) for row in rows})
