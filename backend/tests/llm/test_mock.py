"""Tests for `app.llm.mock` — the deterministic stand-in.

Every assertion here is really about Checkpoint 9: the E2E suite runs with
`LLM_MOCK=true`, so anything the mock does non-deterministically becomes a
flaky browser test that looks like a frontend bug.
"""

from __future__ import annotations

import json

import pytest

from app.llm import parse_reply
from app.llm.mock import mock_completion


def ask(text: str) -> dict:
    return json.loads(mock_completion([{"role": "user", "content": text}]))


class TestDeterminism:
    def test_the_same_message_always_gives_the_same_reply(self):
        assert mock_completion([{"role": "user", "content": "buy 10 AAPL"}]) == mock_completion(
            [{"role": "user", "content": "buy 10 AAPL"}]
        )

    def test_the_output_is_raw_json_the_real_parser_accepts(self):
        """The mock feeds `parse_reply`, not a shortcut around it, so a mock run
        exercises the same parser a live run does."""
        parsed = parse_reply(mock_completion([{"role": "user", "content": "buy 10 AAPL"}]))

        assert parsed.trades[0].ticker == "AAPL"
        assert parsed.rejected == []

    def test_it_reads_the_newest_user_turn_not_the_system_prompt(self):
        """The prompt's context block names every watched ticker. Matching
        against it would make the mock trade on whatever the portfolio happened
        to contain."""
        reply = json.loads(
            mock_completion(
                [
                    {"role": "system", "content": "WATCHLIST\n  AAPL: $200\n  buy 99 TSLA"},
                    {"role": "user", "content": "buy 1 MSFT"},
                    {"role": "assistant", "content": "buy 50 NVDA"},
                    {"role": "user", "content": "buy 2 GOOGL"},
                ]
            )
        )

        assert [t["ticker"] for t in reply["trades"]] == ["GOOGL"]

    def test_no_user_turn_is_answered_not_crashed(self):
        reply = json.loads(mock_completion([{"role": "system", "content": "context"}]))

        assert reply["message"]
        assert reply["trades"] == []


class TestTradeExtraction:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("buy 10 AAPL", ("buy", 10.0, "AAPL")),
            ("sell 5 MSFT", ("sell", 5.0, "MSFT")),
            ("BUY 3 nvda", ("buy", 3.0, "NVDA")),
            ("buy 2.5 shares of TSLA", ("buy", 2.5, "TSLA")),
            ("please sell 1 share GOOGL now", ("sell", 1.0, "GOOGL")),
        ],
    )
    def test_it_recognises_an_order(self, text, expected):
        (trade,) = ask(text)["trades"]

        assert (trade["side"], trade["quantity"], trade["ticker"]) == expected

    def test_several_orders_in_one_message(self):
        trades = ask("buy 1 AAPL and sell 2 MSFT")["trades"]

        assert [(t["side"], t["ticker"]) for t in trades] == [("buy", "AAPL"), ("sell", "MSFT")]

    def test_conversation_without_an_order_trades_nothing(self):
        reply = ask("how is my portfolio doing?")

        assert reply["trades"] == []
        assert reply["watchlist_changes"] == []
        assert reply["message"]


class TestWatchlistExtraction:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("add PYPL", ("PYPL", "add")),
            ("watch SQ", ("SQ", "add")),
            ("remove NFLX", ("NFLX", "remove")),
            ("unwatch V", ("V", "remove")),
        ],
    )
    def test_it_recognises_a_watchlist_verb(self, text, expected):
        (change,) = ask(text)["watchlist_changes"]

        assert (change["ticker"], change["action"]) == expected

    def test_common_words_are_not_mistaken_for_tickers(self):
        """ "add some cash" must not open a watchlist entry for SOME."""
        assert ask("add some cash to the account")["watchlist_changes"] == []

    def test_a_traded_ticker_is_not_also_added(self):
        """ "buy 5 AAPL and add it to my watchlist" — the buy already tracks it,
        and a stray second action would make an E2E assertion on action counts
        wrong for a reason unrelated to the code under test."""
        reply = ask("buy 5 AAPL and add AAPL to my watchlist")

        assert [t["ticker"] for t in reply["trades"]] == ["AAPL"]
        assert reply["watchlist_changes"] == []

    def test_a_repeated_ticker_yields_one_change(self):
        assert len(ask("add PYPL, add PYPL again")["watchlist_changes"]) == 1


class TestMessageText:
    def test_it_names_what_it_did(self):
        """Checkpoint 9 asserts on the rendered bubble, not only on the actions."""
        assert "AAPL" in ask("buy 10 AAPL")["message"]

    def test_the_idle_reply_explains_what_the_mock_understands(self):
        message = ask("hello")["message"]

        assert "Mock assistant" in message
        assert "buy" in message

    def test_it_never_returns_an_empty_message(self):
        """An empty message is `MalformedReplyError`, which would make every
        mock-mode E2E test see the graceful-failure path instead."""
        for text in ["", "   ", "?", "buy 10 AAPL", "add PYPL"]:
            assert ask(text)["message"].strip()
