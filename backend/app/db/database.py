"""SQLite connection handling and lazy initialisation.

PLAN.md §7: there is no migration step and no setup command. The first
connection that finds the tables missing creates and seeds them, so a fresh
Docker volume, a deleted database file, and a first run on a developer laptop
all behave identically.

Threading model: one connection per operation, closed when the operation ends.
FastAPI runs `def` handlers in a worker thread, so a shared connection would
need `check_same_thread=False` plus external serialisation; opening a fresh one
sidesteps the whole question.

That costs about 500 µs, measured — not the "microseconds" one might assume.
Most of it is WAL: because no connection is ever held open, every operation is
the last one to close, so it creates the `-wal`/`-shm` sidecars, checkpoints,
and unlinks them again. Fine for endpoints at human cadence, which is all of
them; worth remembering before putting a database read on the 500 ms tick.
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
from app.paths import REPO_ROOT

logger = logging.getLogger(__name__)

DEFAULT_USER_ID = "default"
STARTING_CASH = 10000.0

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

#: Repo-root `db/finally.db` — the directory PLAN.md §11 volume-mounts to
#: /app/db in the container. In the image the package does not sit under the
#: repo root, so the Dockerfile sets DB_PATH explicitly.
_DEFAULT_DB_PATH = REPO_ROOT / "db" / "finally.db"

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
        _configure(conn)
        ensure_initialized(conn)
        yield conn
    finally:
        conn.close()


def _configure(conn: sqlite3.Connection) -> None:
    """Per-connection PRAGMAs.

    Order matters. busy_timeout comes first because the WAL switch below is
    what needs it: journal_mode is a property of the *file*, so changing it
    takes a brief exclusive lock, and several threads opening a cold database
    at once will collide over it.

    WAL itself lets the SSE stream and the snapshot writer read while a trade
    writes; busy_timeout covers the writer-versus-writer case WAL does not,
    blocking for up to 5 s instead of raising "database is locked".
    """
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        # SQLite returns SQLITE_BUSY for a contended journal_mode change
        # *without* consulting the busy handler, so the timeout above cannot
        # help here. Losing the race is harmless: the connection that won is
        # setting the very same mode on the very same file. Raising instead
        # would turn a burst of first requests into 500s — a browser opening
        # the page fires several before the first one finishes.
        logger.debug("journal_mode=WAL is busy; another connection is setting it")


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


@contextmanager
def read_transaction() -> Iterator[sqlite3.Connection]:
    """A connection holding one consistent read snapshot.

    `connect()` is autocommit, so each statement reads the database as it is at
    that instant. That is fine for a single query and wrong for several: valuing
    the portfolio reads cash and then positions, and a trade committing between
    the two yields pre-trade cash beside a post-trade position — a total that
    never existed, reported to the user as fact.

    BEGIN DEFERRED fixes the snapshot at the first read and holds it to the end.
    Under WAL it takes no write lock, so unlike `transaction()` it does not
    block, or get blocked by, the very trade it is racing.
    """
    with connect() as conn:
        conn.execute("BEGIN DEFERRED")
        try:
            yield conn
        finally:
            # Read-only by contract, so there is nothing to commit. Rollback
            # also releases the snapshot in the error case without pretending
            # the caller's partial work was intended.
            conn.rollback()


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
            UNION ALL
            SELECT ticker FROM positions WHERE user_id = ? AND quantity != 0
            """,
            (user_id, user_id),
        ).fetchall()

    # Deduplicated here, not by the query — hence UNION ALL above rather than
    # UNION, which would sort and dedupe on the raw strings only to have it
    # redone. SQL comparison is case-sensitive, as is the UNIQUE (user_id,
    # ticker) constraint, so 'aapl' and 'AAPL' would both survive it and both
    # normalise to AAPL: the same ticker priced twice, and sent twice in every
    # Massive request.
    return sorted({normalize_ticker(row["ticker"]) for row in rows})
