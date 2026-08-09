"""Tests for the SQLite layer: schema, lazy initialisation, and seeding."""

from __future__ import annotations

import re
import sqlite3
import threading
import uuid
from pathlib import Path

import pytest

from app.db import (
    DEFAULT_USER_ID,
    REQUIRED_TABLES,
    STARTING_CASH,
    connect,
    database,
    ensure_initialized,
    get_db_path,
    load_tracked_tickers,
    transaction,
    utc_now,
)
from app.db.database import _DEFAULT_DB_PATH, _SCHEMA_PATH
from app.market import DEFAULT_TICKERS

# PLAN.md §7 spells these out. Written here as a literal, not imported, so that
# a change to the market module's seed list cannot silently redefine what the
# plan says the default watchlist is.
PLAN_DEFAULT_WATCHLIST = (
    "AAPL",
    "GOOGL",
    "MSFT",
    "AMZN",
    "TSLA",
    "NVDA",
    "META",
    "JPM",
    "V",
    "NFLX",
)


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row["name"] for row in rows}


def add_position(ticker: str, quantity: float, user_id: str = DEFAULT_USER_ID) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO positions (id, user_id, ticker, quantity, avg_cost, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id, ticker, quantity, 100.0, utc_now()),
        )


class TestSchema:
    def test_creates_all_six_tables(self):
        with connect() as conn:
            assert set(REQUIRED_TABLES) <= table_names(conn)

    def test_required_tables_matches_schema_file(self):
        """The initialisation check must know about every table the schema creates.

        A table added to schema.sql but missing from REQUIRED_TABLES would not
        be part of the "is it initialised?" test, so a database missing only
        that table would look healthy and fail at query time.
        """
        declared = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", _SCHEMA_PATH.read_text()))
        assert declared == set(REQUIRED_TABLES)

    def test_columns_match_the_plan(self):
        expected = {
            "users_profile": {"id", "cash_balance", "created_at"},
            "watchlist": {"id", "user_id", "ticker", "added_at"},
            "positions": {"id", "user_id", "ticker", "quantity", "avg_cost", "updated_at"},
            "trades": {"id", "user_id", "ticker", "side", "quantity", "price", "executed_at"},
            "portfolio_snapshots": {"id", "user_id", "total_value", "recorded_at"},
            "chat_messages": {"id", "user_id", "role", "content", "actions", "created_at"},
        }
        with connect() as conn:
            for table, columns in expected.items():
                actual = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
                assert actual == columns, table

    @pytest.mark.parametrize("table", ["watchlist", "positions"])
    def test_ticker_is_unique_per_user(self, table):
        with connect() as conn:
            indexes = conn.execute(f"PRAGMA index_list({table})").fetchall()
            unique_cols = [
                tuple(row["name"] for row in conn.execute(f"PRAGMA index_info({idx['name']})"))
                for idx in indexes
                if idx["unique"]
            ]
        assert ("user_id", "ticker") in unique_cols

    def test_trade_side_is_constrained(self):
        with connect() as conn, pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO trades (id, user_id, ticker, side, quantity, price, executed_at)"
                " VALUES ('t', 'default', 'AAPL', 'hodl', 1, 1.0, 'now')"
            )

    def test_wal_mode_is_enabled(self):
        with connect() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"


class TestSeeding:
    def test_seeds_the_default_profile(self):
        with connect() as conn:
            rows = conn.execute("SELECT * FROM users_profile").fetchall()
        assert len(rows) == 1
        assert rows[0]["id"] == DEFAULT_USER_ID
        assert rows[0]["cash_balance"] == pytest.approx(STARTING_CASH) == pytest.approx(10000.0)

    def test_seeds_the_ten_default_tickers(self):
        with connect() as conn:
            rows = conn.execute("SELECT ticker, user_id FROM watchlist").fetchall()
        assert {row["ticker"] for row in rows} == set(PLAN_DEFAULT_WATCHLIST)
        assert len(rows) == 10
        assert {row["user_id"] for row in rows} == {DEFAULT_USER_ID}

    def test_market_default_tickers_matches_the_plan(self):
        """The simulator's seed prices and the seeded watchlist are one list.

        If they drift, a seeded ticker has no starting price and the watchlist
        renders a row that never gets a quote.
        """
        assert set(DEFAULT_TICKERS) == set(PLAN_DEFAULT_WATCHLIST)


class TestLazyInitialisation:
    def test_is_idempotent_across_repeated_connections(self):
        for _ in range(3):
            with connect() as conn:
                profiles = conn.execute("SELECT COUNT(*) FROM users_profile").fetchone()[0]
                tickers = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
            assert (profiles, tickers) == (1, 10)

    def test_does_not_resurrect_a_deleted_ticker(self):
        """Seeding runs only when tables are missing, so a user who removes
        AAPL does not find it back on the next request."""
        with connect() as conn:
            conn.execute("DELETE FROM watchlist WHERE ticker = 'AAPL'")

        with connect() as conn:
            remaining = {r["ticker"] for r in conn.execute("SELECT ticker FROM watchlist")}
        assert "AAPL" not in remaining

    def test_does_not_overwrite_existing_state(self):
        with connect() as conn:
            conn.execute("UPDATE users_profile SET cash_balance = 42.0")

        with connect() as conn:
            cash = conn.execute("SELECT cash_balance FROM users_profile").fetchone()[0]
        assert cash == pytest.approx(42.0)

    @pytest.mark.parametrize("attempt", [1, 2])
    def test_deleting_the_file_recreates_it(self, attempt, temp_db):
        """PLAN.md Checkpoint 2 exit criterion, run twice in a row.

        Parametrised rather than looped so a failure reports which pass broke:
        an initialisation flag cached in the module would let the first pass
        succeed and the second serve `no such table`.
        """
        with connect() as conn:
            conn.execute("UPDATE users_profile SET cash_balance = 1.0")

        for path in temp_db.parent.glob("test.db*"):  # plus the -wal and -shm sidecars
            path.unlink()
        assert not temp_db.exists()

        with connect() as conn:
            assert set(REQUIRED_TABLES) <= table_names(conn)
            cash = conn.execute("SELECT cash_balance FROM users_profile").fetchone()[0]
            tickers = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]

        assert temp_db.exists()
        assert cash == pytest.approx(STARTING_CASH)  # freshly seeded, not the edited value
        assert tickers == 10

    def test_creates_the_parent_directory(self, monkeypatch, tmp_path):
        nested = tmp_path / "does" / "not" / "exist" / "finally.db"
        monkeypatch.setenv("DB_PATH", str(nested))

        with connect() as conn:
            assert set(REQUIRED_TABLES) <= table_names(conn)
        assert nested.exists()

    def test_the_thread_that_loses_the_race_does_not_reinitialise(self, monkeypatch, temp_db):
        """The re-check inside the lock is what makes losing the race cheap.

        Driven deterministically rather than with threads: the outer check sees
        no tables, and by the time the lock is held another thread has created
        them. Without the second check this thread reruns the schema and reseeds
        a database that is already in use.
        """
        looks = iter([False, True])
        monkeypatch.setattr(database, "_tables_present", lambda conn: next(looks))
        monkeypatch.setattr(
            database,
            "_seed",
            lambda conn: pytest.fail("reseeded a database another thread had just created"),
        )

        conn = sqlite3.connect(temp_db)
        try:
            ensure_initialized(conn)
        finally:
            conn.close()

    def test_a_failure_mid_initialisation_leaves_no_half_built_schema(self, monkeypatch):
        """Schema and seed land together or not at all.

        SQLite makes DDL transactional, so rolling back really does drop the
        tables. Without the rollback the next request would find all six tables
        present, skip initialisation, and run against an unseeded database with
        no profile row and an empty watchlist.
        """

        def failing_seed(conn):
            raise sqlite3.OperationalError("disk I/O error")

        monkeypatch.setattr("app.db.database._seed", failing_seed)

        with pytest.raises(sqlite3.OperationalError):
            with connect():
                pass

        monkeypatch.undo()
        with connect() as conn:
            assert set(REQUIRED_TABLES) <= table_names(conn)
            assert conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0] == 10

    def test_concurrent_first_requests_initialise_once(self):
        """Ten threads racing on a cold database must not seed ten times.

        The realistic trigger is a browser opening the page and firing several
        requests before the first one has finished creating the schema.
        """
        errors: list[BaseException] = []
        barrier = threading.Barrier(10)

        def worker() -> None:
            try:
                barrier.wait(timeout=5)
                with connect() as conn:
                    conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()
            except BaseException as exc:  # noqa: BLE001 - re-raised via the assert below
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert errors == []
        with connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM users_profile").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0] == 10


class TestTransaction:
    def test_commits_on_success(self):
        with transaction() as conn:
            conn.execute("UPDATE users_profile SET cash_balance = 123.0")

        with connect() as conn:
            assert conn.execute("SELECT cash_balance FROM users_profile").fetchone()[0] == 123.0

    def test_rolls_back_every_statement_on_failure(self):
        with pytest.raises(ValueError):
            with transaction() as conn:
                conn.execute("UPDATE users_profile SET cash_balance = 0.0")
                conn.execute("DELETE FROM watchlist")
                raise ValueError("trade validation failed")

        with connect() as conn:
            assert conn.execute("SELECT cash_balance FROM users_profile").fetchone()[0] == 10000.0
            assert conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0] == 10


class TestDbPath:
    def test_reads_the_environment_at_call_time(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DB_PATH", str(tmp_path / "a.db"))
        assert get_db_path() == tmp_path / "a.db"
        monkeypatch.setenv("DB_PATH", str(tmp_path / "b.db"))
        assert get_db_path() == tmp_path / "b.db"

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_falls_back_to_the_repo_default(self, monkeypatch, blank):
        monkeypatch.setenv("DB_PATH", blank)
        assert get_db_path() == _DEFAULT_DB_PATH

    def test_default_points_at_the_volume_mount_directory(self):
        """PLAN.md §4: the SQLite file lives in the repo-root `db/`, which §11
        volume-mounts into the container."""
        assert _DEFAULT_DB_PATH.name == "finally.db"
        assert _DEFAULT_DB_PATH.parent.name == "db"
        assert (_DEFAULT_DB_PATH.parent.parent / "backend" / "app").is_dir()


class TestLoadTrackedTickers:
    def test_returns_the_seeded_watchlist(self):
        assert load_tracked_tickers() == sorted(PLAN_DEFAULT_WATCHLIST)

    def test_includes_held_positions_outside_the_watchlist(self):
        """MARKET_DATA_DESIGN.md §13.4: a position outlives its watchlist row,
        and must keep being priced or the portfolio total silently loses it."""
        add_position("PYPL", 5.0)
        assert "PYPL" in load_tracked_tickers()

    def test_does_not_duplicate_a_ticker_that_is_both(self):
        add_position("AAPL", 3.0)
        tickers = load_tracked_tickers()
        assert tickers.count("AAPL") == 1

    def test_excludes_a_fully_sold_position(self):
        add_position("ZZZZ", 0.0)
        assert "ZZZZ" not in load_tracked_tickers()

    def test_normalises_and_sorts(self):
        """A lower-case row from any writer must not reach a case-sensitive API."""
        with connect() as conn:
            conn.execute(
                "INSERT INTO watchlist (id, user_id, ticker, added_at) VALUES (?,?,?,?)",
                (str(uuid.uuid4()), DEFAULT_USER_ID, " pypl ", utc_now()),
            )
        tickers = load_tracked_tickers()
        assert "PYPL" in tickers
        assert tickers == sorted(tickers)

    def test_ignores_another_users_rows(self):
        add_position("OTHR", 5.0, user_id="someone-else")
        assert "OTHR" not in load_tracked_tickers()


def test_schema_file_ships_with_the_package():
    """The schema is data, not code — a wheel that omits it initialises nothing."""
    assert _SCHEMA_PATH.is_file()
    assert _SCHEMA_PATH == Path(__file__).resolve().parents[2] / "app" / "db" / "schema.sql"
