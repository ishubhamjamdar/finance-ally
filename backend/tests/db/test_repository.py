"""Tests for `app.db.repository` — the row-level reads and writes.

The behaviour asserted here is what `app.portfolio` depends on and does not
re-check: that a position row disappears at quantity zero, that the watchlist
insert reports whether it did anything, and that every query is scoped by
`user_id`.
"""

from __future__ import annotations

import pytest

from app.db import (
    DEFAULT_USER_ID,
    add_watchlist_entry,
    apply_position,
    connect,
    count_watchlist,
    delete_watchlist_entry,
    get_cash_balance,
    get_position,
    insert_chat_message,
    insert_snapshot,
    insert_trade,
    list_chat_messages,
    list_positions,
    list_snapshots,
    list_trades,
    list_watchlist,
    set_cash_balance,
    transaction,
)


class TestProfile:
    def test_round_trips_the_cash_balance(self):
        with connect() as conn:
            assert get_cash_balance(conn) == 10000.0
            set_cash_balance(conn, 1234.56)
            assert get_cash_balance(conn) == 1234.56

    def test_raises_for_a_user_with_no_profile_row(self):
        """Not a silent zero. A missing profile means lazy init did not
        complete, and inventing a balance would hide that behind a portfolio
        that reads as merely broke."""
        with connect() as conn, pytest.raises(LookupError, match="nobody"):
            get_cash_balance(conn, "nobody")


class TestPositions:
    def test_inserts_then_updates_in_place(self):
        with connect() as conn:
            apply_position(conn, "AAPL", 5, 100.0)
            apply_position(conn, "AAPL", 8, 125.0)

            held = get_position(conn, "AAPL")
            assert (held.quantity, held.avg_cost) == (8, 125.0)
            assert len(list_positions(conn)) == 1

    def test_quantity_zero_deletes_the_row(self):
        """One function owns the rule, so no caller can leave a 0-share row
        behind — a blank line in the positions table and a zero-area tile in
        the heatmap."""
        with connect() as conn:
            apply_position(conn, "AAPL", 5, 100.0)
            apply_position(conn, "AAPL", 0, 100.0)

            assert get_position(conn, "AAPL") is None
            assert list_positions(conn) == []

    def test_normalises_the_ticker_on_every_path(self):
        with connect() as conn:
            apply_position(conn, "aapl", 5, 100.0)
            assert get_position(conn, "AAPL").ticker == "AAPL"
            assert get_position(conn, " aapl ") is not None

            apply_position(conn, "aapl", 0, 100.0)
            assert get_position(conn, "AAPL") is None

    def test_lists_in_ticker_order(self):
        with connect() as conn:
            for ticker in ("MSFT", "AAPL", "TSLA"):
                apply_position(conn, ticker, 1, 10.0)
            assert [p.ticker for p in list_positions(conn)] == ["AAPL", "MSFT", "TSLA"]

    def test_scopes_by_user(self):
        with connect() as conn:
            apply_position(conn, "AAPL", 5, 100.0, user_id="other")
            assert list_positions(conn) == []
            assert get_position(conn, "AAPL") is None
            assert len(list_positions(conn, user_id="other")) == 1


class TestTrades:
    def test_appends_and_reads_back_newest_first(self):
        with connect() as conn:
            insert_trade(conn, "AAPL", "buy", 2, 100.0)
            insert_trade(conn, "GOOGL", "sell", 1, 50.0)

            assert [t.ticker for t in list_trades(conn)] == ["GOOGL", "AAPL"]

    def test_honours_the_limit(self):
        with connect() as conn:
            for _ in range(5):
                insert_trade(conn, "AAPL", "buy", 1, 100.0)
            assert len(list_trades(conn, limit=2)) == 2

    def test_the_schema_rejects_an_unknown_side(self):
        """A CHECK constraint, so a bug that bypassed `app.portfolio` still
        cannot write `side='short'` into the blotter."""
        import sqlite3

        with connect() as conn, pytest.raises(sqlite3.IntegrityError):
            insert_trade(conn, "AAPL", "short", 1, 100.0)


class TestSnapshots:
    def test_returns_oldest_first(self):
        with connect() as conn:
            for value in (1.0, 2.0, 3.0):
                insert_snapshot(conn, value)
            assert [s.total_value for s in list_snapshots(conn, limit=100)] == [1.0, 2.0, 3.0]

    def test_a_limit_keeps_the_newest_points(self):
        """Truncating from the old end: a chart missing its left edge is
        readable, one missing everything since the last trade is not."""
        with connect() as conn:
            for value in (1.0, 2.0, 3.0, 4.0):
                insert_snapshot(conn, value)
            assert [s.total_value for s in list_snapshots(conn, limit=2)] == [3.0, 4.0]

    def test_orders_ties_by_insertion(self):
        """Two snapshots can share a timestamp — a trade landing on the
        background task's tick — and `recorded_at` alone would order them
        arbitrarily, making the P&L line jump backwards."""
        with transaction() as conn:
            for value in (1.0, 2.0, 3.0):
                conn.execute(
                    "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at)"
                    " VALUES (?, ?, ?, ?)",
                    (f"id-{value}", DEFAULT_USER_ID, value, "2026-01-01T00:00:00+00:00"),
                )

        with connect() as conn:
            assert [s.total_value for s in list_snapshots(conn, limit=100)] == [1.0, 2.0, 3.0]
            assert [s.total_value for s in list_snapshots(conn, limit=2)] == [2.0, 3.0]


class TestWatchlist:
    def test_add_reports_the_new_entry(self):
        with connect() as conn:
            entry = add_watchlist_entry(conn, "pypl")

            assert entry.ticker == "PYPL"
            assert entry.added_at
            assert "PYPL" in [e.ticker for e in list_watchlist(conn)]

    def test_add_reports_none_for_a_duplicate(self):
        """The UNIQUE constraint decides, not a prior SELECT — two concurrent
        adds would both pass a SELECT and one would then raise."""
        with connect() as conn:
            assert add_watchlist_entry(conn, "AAPL") is None
            assert [e.ticker for e in list_watchlist(conn)].count("AAPL") == 1

    def test_delete_reports_whether_it_removed_anything(self):
        with connect() as conn:
            assert delete_watchlist_entry(conn, "aapl") is True
            assert delete_watchlist_entry(conn, "AAPL") is False

    def test_lists_in_add_order(self):
        with connect() as conn:
            add_watchlist_entry(conn, "PYPL")
            assert [e.ticker for e in list_watchlist(conn)][-1] == "PYPL"

    def test_scopes_by_user(self):
        with connect() as conn:
            add_watchlist_entry(conn, "PYPL", user_id="other")
            assert "PYPL" not in [e.ticker for e in list_watchlist(conn)]
            assert [e.ticker for e in list_watchlist(conn, user_id="other")] == ["PYPL"]


class TestCountWatchlist:
    def test_it_counts_the_seeded_rows(self):
        with connect() as conn:
            assert count_watchlist(conn) == 10

    def test_it_tracks_additions_and_removals(self):
        with connect() as conn:
            add_watchlist_entry(conn, "PYPL")
            assert count_watchlist(conn) == 11
            delete_watchlist_entry(conn, "PYPL")
            assert count_watchlist(conn) == 10

    def test_it_scopes_by_user(self):
        with connect() as conn:
            add_watchlist_entry(conn, "PYPL", user_id="other")
            assert count_watchlist(conn, user_id="other") == 1


class TestChatMessages:
    def test_a_message_round_trips(self):
        with connect() as conn:
            written = insert_chat_message(conn, "user", "hello")
            (read,) = list_chat_messages(conn, limit=10)

        assert (read.id, read.role, read.content) == (written.id, "user", "hello")
        assert read.actions is None

    def test_actions_are_stored_as_json_and_returned_decoded(self):
        """The column is JSON by PLAN.md §7. Encoding in the repository is what
        keeps the writer and the reader on one format — a caller passing a
        pre-encoded string would leave the reader guessing."""
        actions = [{"kind": "trade", "ok": True, "ticker": "AAPL"}]

        with connect() as conn:
            insert_chat_message(conn, "assistant", "Bought.", actions)
            (read,) = list_chat_messages(conn, limit=10)
            raw = conn.execute("SELECT actions FROM chat_messages").fetchone()[0]

        assert read.actions == actions
        assert isinstance(raw, str)

    def test_an_empty_action_list_is_stored_as_null(self):
        """`None` and `[]` mean the same thing — nothing was executed — so they
        are stored the same way rather than as two states the reader must
        distinguish."""
        with connect() as conn:
            insert_chat_message(conn, "assistant", "Just talking.", [])
            assert conn.execute("SELECT actions FROM chat_messages").fetchone()[0] is None

    def test_messages_come_back_oldest_first(self):
        with connect() as conn:
            for n in range(4):
                insert_chat_message(conn, "user", f"message {n}")
            assert [m.content for m in list_chat_messages(conn, limit=10)] == [
                f"message {n}" for n in range(4)
            ]

    def test_the_limit_keeps_the_newest(self):
        """Truncation from the wrong end would replay the start of a long
        conversation and drop everything the user just said."""
        with connect() as conn:
            for n in range(5):
                insert_chat_message(conn, "user", f"message {n}")
            assert [m.content for m in list_chat_messages(conn, limit=2)] == [
                "message 3",
                "message 4",
            ]

    def test_a_question_and_its_answer_keep_their_order(self):
        """Both rows are written inside one transaction and can share a
        timestamp to the microsecond; without the rowid tiebreak they would
        replay as the answer preceding the question."""
        with transaction() as conn:
            insert_chat_message(conn, "user", "question")
            insert_chat_message(conn, "assistant", "answer")

        with connect() as conn:
            assert [m.role for m in list_chat_messages(conn, limit=10)] == ["user", "assistant"]

    def test_it_scopes_by_user(self):
        with connect() as conn:
            insert_chat_message(conn, "user", "theirs", user_id="other")
            assert list_chat_messages(conn, limit=10) == []
            assert len(list_chat_messages(conn, limit=10, user_id="other")) == 1

    def test_an_undecodable_actions_column_does_not_break_replay(self):
        """A raised JSONDecodeError here would not cost one message: history is
        read on every later chat request, so one bad row would make the endpoint
        permanently unusable."""
        with connect() as conn:
            insert_chat_message(conn, "assistant", "Bought.", [{"ok": True}])
            conn.execute("UPDATE chat_messages SET actions = ?", ("{not json",))
            (read,) = list_chat_messages(conn, limit=10)

        assert read.actions is None
        assert read.content == "Bought."

    def test_a_non_list_actions_column_is_ignored(self):
        """The shape is a list by contract. An object would reach the frontend
        as something it cannot iterate."""
        with connect() as conn:
            insert_chat_message(conn, "assistant", "Bought.", [{"ok": True}])
            conn.execute("UPDATE chat_messages SET actions = ?", ('{"ok": true}',))
            (read,) = list_chat_messages(conn, limit=10)

        assert read.actions is None
