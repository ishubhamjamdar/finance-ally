"""Tests for `app.chat` — the turn, and what the model is allowed to do with it.

Driven against `handle_message` rather than HTTP, for the same reason
`test_portfolio.py` and `test_watchlist.py` are: this is the layer that decides
what a model's output is permitted to change, and it has to hold whether the
request arrived over HTTP or not.

The recurring theme: the model is a client, not an authority. Every test that
looks like "the LLM asked for something impossible" is checking that the answer
came from `app.portfolio` or `app.watchlist`, not from this module.
"""

from __future__ import annotations

import json

import pytest

from app.chat import MALFORMED_REPLY_MESSAGE, get_transcript, handle_message
from app.db import DEFAULT_USER_ID, connect, get_position, list_watchlist
from app.llm import MAX_HISTORY_MESSAGES, LLMUnavailableError
from app.watchlist import MAX_WATCHLIST_SIZE
from tests.conftest import PLAN_DEFAULT_WATCHLIST, RecordingSource, stored_messages


@pytest.fixture
def source(price_cache):
    """In sync with the seeded watchlist, so reconcile records only the change
    a test actually made."""
    return RecordingSource(price_cache, tickers=list(PLAN_DEFAULT_WATCHLIST))


@pytest.fixture
def chat(price_cache, source, stub_model):
    """Run one turn against the fixed cache and the recording source."""

    async def _chat(text: str = "hello", user_id: str = DEFAULT_USER_ID):
        return await handle_message(price_cache, source, text, user_id)

    return _chat


def watched() -> list[str]:
    with connect() as conn:
        return [entry.ticker for entry in list_watchlist(conn)]


def held(ticker: str):
    with connect() as conn:
        return get_position(conn, ticker)


class TestConversation:
    async def test_a_plain_reply_comes_back_with_no_actions(self, chat, stub_model):
        stub_model.replies(message="You are entirely in cash.")

        reply = await chat("how am I doing?")

        assert reply.message == "You are entirely in cash."
        assert reply.actions == []
        assert reply.portfolio.cash_balance == 10000.0

    async def test_the_users_text_reaches_the_model_as_the_final_turn(self, chat, stub_model):
        await chat("what should I buy?")

        assert stub_model.messages[-1] == {"role": "user", "content": "what should I buy?"}

    async def test_the_prompt_carries_the_live_portfolio(self, chat, stub_model, add_position):
        add_position("AAPL", quantity=5.0)

        await chat("status")

        context = stub_model.messages[1]["content"]
        assert context.startswith("PORTFOLIO CONTEXT")
        assert "AAPL" in context

    async def test_the_message_is_stripped_before_it_is_sent_or_stored(self, chat, stub_model):
        await chat("   spaced out   ")

        assert stub_model.messages[-1]["content"] == "spaced out"
        assert stored_messages()[0][1] == "spaced out"


class TestTradeExecution:
    async def test_a_requested_trade_moves_cash_and_positions(self, chat, stub_model, read_cash):
        """PLAN.md §Checkpoint 4's first exit criterion: a trade the model asks
        for actually lands in the ledger."""
        stub_model.replies(
            message="Bought.", trades=[{"ticker": "AAPL", "side": "buy", "quantity": 10}]
        )

        reply = await chat("buy 10 AAPL")

        assert read_cash() == 8000.0  # 10 × $200
        assert held("AAPL").quantity == 10.0
        (action,) = reply.actions
        assert action.ok is True
        assert action.kind == "trade"
        assert action.result["price"] == 200.0

    async def test_the_returned_portfolio_reflects_the_trade(self, chat, stub_model):
        """The reply is what Checkpoint 7 repaints every panel from. A cash
        balance read before the fill would render a position beside the money
        that bought it."""
        stub_model.replies(trades=[{"ticker": "AAPL", "side": "buy", "quantity": 10}])

        reply = await chat("buy")

        assert reply.portfolio.cash_balance == 8000.0
        assert [p.ticker for p in reply.portfolio.positions] == ["AAPL"]

    async def test_a_trade_that_fails_validation_is_reported_not_lost(
        self, chat, stub_model, read_cash
    ):
        """Exit criterion: the error comes back in the response rather than
        silently vanishing — and the money does not move."""
        stub_model.replies(
            message="Buying big.", trades=[{"ticker": "AAPL", "side": "buy", "quantity": 1000}]
        )

        reply = await chat("buy 1000 AAPL")

        assert reply.message == "Buying big."
        (action,) = reply.actions
        assert action.ok is False
        assert "Insufficient cash" in action.detail
        assert read_cash() == 10000.0

    async def test_the_model_cannot_trade_on_a_frozen_price(
        self, chat, stub_model, price_cache, read_cash
    ):
        """The staleness bound protects the chat path too, and for a better
        reason than the trade bar: nobody is watching this one.

        A user typing into the trade bar can see the prices are not moving. A
        model asked to rebalance cannot, and would happily fill ten orders
        against a wedged feed.
        """
        price_cache.staleness_limit = 10.0
        price_cache._received["AAPL"] -= 120
        stub_model.replies(trades=[{"ticker": "AAPL", "side": "buy", "quantity": 1}])

        reply = await chat("buy some AAPL")

        (action,) = reply.actions
        assert action.ok is False
        assert "stopped updating" in action.detail
        assert read_cash() == 10000.0

    async def test_the_model_cannot_oversell(self, chat, stub_model, add_position):
        add_position("AAPL", quantity=2.0)
        stub_model.replies(trades=[{"ticker": "AAPL", "side": "sell", "quantity": 50}])

        (action,) = (await chat("sell it all")).actions

        assert action.ok is False
        assert "only 2 held" in action.detail
        assert held("AAPL").quantity == 2.0

    async def test_the_model_cannot_trade_an_unpriced_ticker(self, chat, stub_model):
        stub_model.replies(trades=[{"ticker": "ZZZZ", "side": "buy", "quantity": 1}])

        (action,) = (await chat("buy ZZZZ")).actions

        assert action.ok is False
        assert "No price available" in action.detail

    async def test_one_failure_does_not_stop_the_others(self, chat, stub_model, read_cash):
        """ "Sell AAPL and buy MSFT" should still buy MSFT when the AAPL
        position turns out to be gone, and say so about both."""
        stub_model.replies(
            trades=[
                {"ticker": "AAPL", "side": "sell", "quantity": 5},  # nothing held
                {"ticker": "MSFT", "side": "buy", "quantity": 2},  # $800
            ]
        )

        reply = await chat("rotate")

        assert [a.ok for a in reply.actions] == [False, True]
        assert read_cash() == 9200.0

    async def test_trades_are_applied_in_order_against_a_moving_balance(
        self, chat, stub_model, read_cash
    ):
        """Sequential, not concurrent: the second buy is checked against the
        balance the first one left, so the pair cannot overspend together."""
        stub_model.replies(
            trades=[
                {"ticker": "MSFT", "side": "buy", "quantity": 20},  # $8,000 — fits
                {"ticker": "MSFT", "side": "buy", "quantity": 20},  # $8,000 — does not
            ]
        )

        reply = await chat("buy twice")

        assert [a.ok for a in reply.actions] == [True, False]
        assert read_cash() == 2000.0

    async def test_fractional_quantities_survive_the_round_trip(self, chat, stub_model):
        stub_model.replies(trades=[{"ticker": "AAPL", "side": "buy", "quantity": 0.5}])

        await chat("buy a half")

        assert held("AAPL").quantity == 0.5

    async def test_a_lowercase_ticker_is_normalised(self, chat, stub_model):
        stub_model.replies(trades=[{"ticker": "aapl", "side": "buy", "quantity": 1}])

        await chat("buy aapl")

        assert held("AAPL").quantity == 1.0


class TestWatchlistChanges:
    async def test_an_addition_is_stored_and_subscribed(self, chat, stub_model, source):
        stub_model.replies(watchlist_changes=[{"ticker": "PYPL", "action": "add"}])

        (action,) = (await chat("watch PYPL")).actions

        assert action.ok is True
        assert "PYPL" in watched()
        assert "PYPL" in source.added

    async def test_a_removal_is_stored_and_unsubscribed(self, chat, stub_model, source):
        stub_model.replies(watchlist_changes=[{"ticker": "NFLX", "action": "remove"}])

        (action,) = (await chat("drop NFLX")).actions

        assert action.ok is True
        assert "NFLX" not in watched()
        assert "NFLX" in source.removed

    async def test_removing_a_held_ticker_says_it_keeps_streaming(
        self, chat, stub_model, add_position
    ):
        """It stays tracked because it is still held. A user told only
        "removed" would think the still-arriving price was a bug."""
        add_position("AAPL", quantity=5.0)
        stub_model.replies(watchlist_changes=[{"ticker": "AAPL", "action": "remove"}])

        (action,) = (await chat("stop watching AAPL")).actions

        assert action.ok is True
        assert "still held" in action.detail
        assert held("AAPL") is not None

    async def test_a_duplicate_addition_is_reported_not_raised(self, chat, stub_model):
        """`app.watchlist` raising through the chat handler would abort the whole
        reply with a 409 instead of saying "AAPL was already watched"."""
        stub_model.replies(
            message="Adding.", watchlist_changes=[{"ticker": "AAPL", "action": "add"}]
        )

        reply = await chat("watch AAPL")

        assert reply.message == "Adding."
        assert reply.actions[0].ok is False
        assert "already on the watchlist" in reply.actions[0].detail

    async def test_removing_an_unwatched_ticker_is_reported(self, chat, stub_model):
        stub_model.replies(watchlist_changes=[{"ticker": "PYPL", "action": "remove"}])

        assert (await chat("drop PYPL")).actions[0].ok is False

    async def test_the_size_cap_stops_a_looping_model(self, chat, stub_model, source):
        """Checkpoint 3 carried this forward precisely because Checkpoint 4
        hands the watchlist to something that can call it repeatedly."""
        fillers = [
            {"ticker": f"T{n:03d}", "action": "add"}
            for n in range(MAX_WATCHLIST_SIZE - len(PLAN_DEFAULT_WATCHLIST) + 5)
        ]
        stub_model.replies(watchlist_changes=fillers[:10])
        await chat("add ten")

        results = []
        for batch in (fillers[10:20], fillers[20:30], fillers[30:40], fillers[40:45]):
            if not batch:
                continue
            stub_model.replies(watchlist_changes=batch)
            results += (await chat("keep going")).actions

        assert len(watched()) == MAX_WATCHLIST_SIZE
        refusals = [a for a in results if not a.ok]
        assert refusals
        assert "full" in refusals[0].detail


class TestActionOrdering:
    async def test_a_watchlist_addition_precedes_a_trade_in_the_same_turn(
        self, chat, stub_model, read_cash
    ):
        """ "Add PYPL and buy 5" has to work in one turn: the add is what makes
        the ticker priceable, and a trade run first would be refused for having
        no price."""
        stub_model.replies(
            watchlist_changes=[{"ticker": "PYPL", "action": "add"}],
            trades=[{"ticker": "PYPL", "side": "buy", "quantity": 5}],
        )

        reply = await chat("add PYPL and buy 5")

        assert [a.ok for a in reply.actions] == [True, True]
        assert [a.kind for a in reply.actions] == ["watchlist", "trade"]
        assert read_cash() == 9750.0  # 5 × $50, the price RecordingSource seeds

    async def test_a_removal_follows_a_trade_of_the_same_ticker(self, chat, stub_model, read_cash):
        """The other half, and the one an earlier version got wrong. A remove
        evicts the ticker from the cache, so run first it would delete the price
        the buy in the same reply needed and refuse the trade."""
        stub_model.replies(
            watchlist_changes=[{"ticker": "AAPL", "action": "remove"}],
            trades=[{"ticker": "AAPL", "side": "buy", "quantity": 2}],
        )

        reply = await chat("buy 2 AAPL then stop watching it")

        assert [a.kind for a in reply.actions] == ["trade", "watchlist"]
        assert [a.ok for a in reply.actions] == [True, True]
        assert read_cash() == 9600.0  # 2 × $200 — the trade filled

    async def test_a_sell_out_and_remove_in_one_turn_still_fills(
        self, chat, stub_model, add_position, read_cash
    ):
        """The phrasing a user would actually reach for. Selling the whole
        position also stops it being tracked, so ordering the remove first would
        strand the sell with no price."""
        add_position("AAPL", quantity=3.0)
        stub_model.replies(
            trades=[{"ticker": "AAPL", "side": "sell", "quantity": 3}],
            watchlist_changes=[{"ticker": "AAPL", "action": "remove"}],
        )

        reply = await chat("sell all my AAPL and take it off the watchlist")

        assert all(a.ok for a in reply.actions)
        assert read_cash() == 10600.0
        assert held("AAPL") is None

    async def test_adds_and_removes_in_one_reply_both_land(self, chat, stub_model):
        stub_model.replies(
            watchlist_changes=[
                {"ticker": "PYPL", "action": "add"},
                {"ticker": "NFLX", "action": "remove"},
            ]
        )

        reply = await chat("swap NFLX for PYPL")

        assert all(a.ok for a in reply.actions)
        assert "PYPL" in watched()
        assert "NFLX" not in watched()


class TestTickerNormalisation:
    """The schema accepts "aapl" and the domain layer upper-cases it, so an
    un-normalised echo would report two spellings of one symbol — and
    Checkpoint 7 matches actions to watchlist and position rows by this field."""

    async def test_a_trade_reports_the_normalised_ticker(self, chat, stub_model):
        stub_model.replies(trades=[{"ticker": "aapl", "side": "buy", "quantity": 1}])

        (action,) = (await chat("buy aapl")).actions

        assert action.ticker == "AAPL"
        assert action.result["ticker"] == "AAPL"
        assert "AAPL" in action.summary
        assert "aapl" not in action.detail

    async def test_a_refused_trade_reports_the_normalised_ticker(self, chat, stub_model):
        stub_model.replies(trades=[{"ticker": "aapl", "side": "buy", "quantity": 9999}])

        (action,) = (await chat("buy aapl")).actions

        assert action.ok is False
        assert action.ticker == "AAPL"

    async def test_a_watchlist_add_reports_the_normalised_ticker(self, chat, stub_model):
        stub_model.replies(watchlist_changes=[{"ticker": "pypl", "action": "add"}])

        (action,) = (await chat("watch pypl")).actions

        assert action.ticker == "PYPL"
        assert action.result["ticker"] == "PYPL"
        assert "pypl" not in action.detail

    async def test_a_watchlist_remove_reports_the_normalised_ticker(self, chat, stub_model):
        """The branch that disagreed with the add branch: it echoed the raw
        string while the add reported `entry.ticker`."""
        stub_model.replies(watchlist_changes=[{"ticker": "nflx", "action": "remove"}])

        (action,) = (await chat("drop nflx")).actions

        assert action.ticker == "NFLX"
        assert action.result["ticker"] == "NFLX"
        assert "nflx" not in action.detail

    async def test_the_stored_actions_carry_the_normalised_ticker(self, chat, stub_model):
        """`chat_messages.actions` is replayed and rendered; a lower-case ticker
        there outlives the request that produced it."""
        stub_model.replies(trades=[{"ticker": "aapl", "side": "buy", "quantity": 1}])

        await chat("buy aapl")

        assert json.loads(stored_messages()[1][2])[0]["ticker"] == "AAPL"


class TestMalformedReplies:
    @pytest.mark.parametrize(
        "raw",
        ["", "not json", "[1,2,3]", '{"trades": []}', '{"message": ""}', '{"message": null}'],
    )
    async def test_an_unusable_reply_becomes_a_graceful_message(self, chat, stub_model, raw):
        """PLAN.md §Checkpoint 4: never a 500. At this layer that means never an
        exception — the user gets a reply that says the turn went wrong."""
        stub_model.replies_raw(raw)

        reply = await chat("hello")

        assert reply.message == MALFORMED_REPLY_MESSAGE
        assert reply.actions == []

    async def test_an_unusable_reply_is_still_recorded(self, chat, stub_model):
        """The exchange happened, so history has to show it — otherwise the
        next turn replays a conversation with a question and no answer."""
        stub_model.replies_raw("garbage")

        await chat("hello")

        assert [(role, content) for role, content, _ in stored_messages()] == [
            ("user", "hello"),
            ("assistant", MALFORMED_REPLY_MESSAGE),
        ]

    async def test_a_malformed_action_is_reported_beside_the_good_ones(
        self, chat, stub_model, read_cash
    ):
        stub_model.replies(
            message="Two things.",
            trades=[
                {"ticker": "AAPL", "side": "buy", "quantity": 1},
                {"ticker": "AAPL", "side": "buy", "quantity": -5},
            ],
        )

        reply = await chat("buy")

        assert reply.message == "Two things."
        assert [a.ok for a in reply.actions] == [True, False]
        assert "quantity" in reply.actions[1].detail
        assert read_cash() == 9800.0

    async def test_a_rejected_action_carries_no_fabricated_ticker(self, chat, stub_model):
        """It failed before a ticker could be read, so reporting one would be
        inventing it."""
        stub_model.replies_raw(json.dumps({"message": "?", "trades": ["nonsense"]}))

        (action,) = (await chat("hi")).actions

        assert action.ok is False
        assert action.ticker is None
        assert action.action is None


class TestProviderFailure:
    async def test_an_unreachable_provider_raises(self, chat, stub_model):
        """Distinct from a bad reply: the endpoint turns this into a 503, and
        resending the same message is the right response."""
        stub_model.fails(LLMUnavailableError("provider is down"))

        with pytest.raises(LLMUnavailableError):
            await chat("hello")

    async def test_nothing_is_persisted_when_the_provider_fails(self, chat, stub_model):
        """History must not claim a turn that never happened — it would be
        replayed into every later prompt in the session."""
        stub_model.fails(LLMUnavailableError("provider is down"))

        with pytest.raises(LLMUnavailableError):
            await chat("hello")

        assert stored_messages() == []


class TestPersistenceAndReplay:
    async def test_both_turns_are_written(self, chat, stub_model):
        stub_model.replies(message="Noted.")

        await chat("remember this")

        assert [(role, content) for role, content, _ in stored_messages()] == [
            ("user", "remember this"),
            ("assistant", "Noted."),
        ]

    async def test_a_user_turn_never_carries_actions(self, chat, stub_model):
        stub_model.replies(trades=[{"ticker": "AAPL", "side": "buy", "quantity": 1}])

        await chat("buy")

        (user_row, assistant_row) = stored_messages()
        assert user_row[2] is None
        assert json.loads(assistant_row[2])[0]["ticker"] == "AAPL"

    async def test_the_conversation_is_replayed_into_the_next_prompt(self, chat, stub_model):
        """The fourth exit criterion. The model has to see what it already said,
        or "yes, do that" refers to nothing."""
        stub_model.replies(message="I suggest buying AAPL.")
        await chat("what should I buy?")

        stub_model.replies(message="Done.")
        await chat("yes, do that")

        replayed = [m for m in stub_model.messages if m["role"] in ("user", "assistant")]
        assert [m["content"] for m in replayed] == [
            "what should I buy?",
            "I suggest buying AAPL.",
            "yes, do that",
        ]

    async def test_replayed_history_is_bounded(self, chat, stub_model):
        for n in range(MAX_HISTORY_MESSAGES):
            stub_model.replies(message=f"reply {n}")
            await chat(f"message {n}")

        replayed = [m for m in stub_model.messages if m["role"] in ("user", "assistant")]
        assert len(replayed) == MAX_HISTORY_MESSAGES + 1  # the history plus the new turn

    async def test_the_newest_turns_are_the_ones_kept(self, chat, stub_model):
        """Truncation from the wrong end would replay the oldest exchanges and
        drop everything the user just said.

        History is read *before* the new turn is persisted, so on call `n` the
        newest replayed row is reply `n-1`, not reply `n`. That ordering is the
        point rather than an accident: the model must not be shown its own
        answer to the question it is being asked.
        """
        last = MAX_HISTORY_MESSAGES - 1
        for n in range(MAX_HISTORY_MESSAGES):
            stub_model.replies(message=f"reply {n}")
            await chat(f"message {n}")

        contents = [m["content"] for m in stub_model.messages if m["role"] in ("user", "assistant")]
        assert contents[-1] == f"message {last}"
        assert contents[-2] == f"reply {last - 1}"
        assert "message 0" not in contents
        assert "reply 0" not in contents


class TestTranscript:
    async def test_it_returns_the_stored_conversation_oldest_first(self, chat, stub_model):
        stub_model.replies(message="First reply.")
        await chat("first")
        stub_model.replies(message="Second reply.")
        await chat("second")

        transcript = get_transcript()

        assert [m["content"] for m in transcript] == [
            "first",
            "First reply.",
            "second",
            "Second reply.",
        ]

    async def test_actions_come_back_decoded(self, chat, stub_model):
        stub_model.replies(trades=[{"ticker": "AAPL", "side": "buy", "quantity": 1}])
        await chat("buy")

        assistant = get_transcript()[-1]

        assert assistant["actions"][0]["ticker"] == "AAPL"
        assert assistant["actions"][0]["ok"] is True

    async def test_a_user_turn_has_null_actions(self, chat, stub_model):
        await chat("hello")

        assert get_transcript()[0]["actions"] is None

    async def test_the_limit_keeps_the_newest_turns(self, chat, stub_model):
        for n in range(4):
            stub_model.replies(message=f"reply {n}")
            await chat(f"message {n}")

        assert [m["content"] for m in get_transcript(limit=2)] == ["message 3", "reply 3"]

    def test_an_empty_conversation_is_an_empty_list(self):
        assert get_transcript() == []


class TestPersistenceFailure:
    """A transcript write that fails must not undo a trade that succeeded."""

    async def test_a_completed_trade_is_still_reported(
        self, chat, stub_model, monkeypatch, read_cash
    ):
        """By the time the transcript is written the fill has committed.
        Raising here would return a 500 for a request that moved cash, and the
        obvious client response to a 500 — resend — would buy twice."""
        stub_model.replies(
            message="Bought.", trades=[{"ticker": "AAPL", "side": "buy", "quantity": 1}]
        )

        def explode(*args, **kwargs):
            raise RuntimeError("database is locked")

        monkeypatch.setattr("app.chat._persist", explode)

        reply = await chat("buy 1 AAPL")

        assert reply.message == "Bought."
        assert reply.actions[0].ok is True
        assert read_cash() == 9800.0

    async def test_the_failure_is_logged(self, chat, stub_model, monkeypatch, caplog):
        def explode(*args, **kwargs):
            raise RuntimeError("database is locked")

        monkeypatch.setattr("app.chat._persist", explode)

        with caplog.at_level("ERROR"):
            await chat("hello")

        assert "Could not record the chat exchange" in caplog.text
