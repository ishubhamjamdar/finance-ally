"""Tests for `app.llm.schema` — parsing untrusted model output.

This is the boundary between a third party's text and the trade path, so the
tests are written as an attacker's checklist as much as a parser's: every way a
reply can be wrong should end as either a rejection the user is shown or a
`MalformedReplyError`, and never as an action that reaches `execute_trade`
carrying something the schema does not allow.
"""

from __future__ import annotations

import json

import pytest

from app.llm import (
    MAX_TRADES_PER_REPLY,
    MAX_WATCHLIST_CHANGES_PER_REPLY,
    AssistantReply,
    MalformedReplyError,
    parse_reply,
    wire_schema,
)
from app.llm.schema import _UNSUPPORTED_SCHEMA_KEYWORDS


def reply(**payload) -> str:
    return json.dumps(payload)


class TestMessage:
    def test_a_plain_conversational_reply_carries_no_actions(self):
        parsed = parse_reply(reply(message="You hold 3 positions."))

        assert parsed.message == "You hold 3 positions."
        assert parsed.trades == []
        assert parsed.watchlist_changes == []
        assert parsed.rejected == []

    def test_the_message_is_stripped(self):
        assert parse_reply(reply(message="  hello  ")).message == "hello"

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            None,
            "not json at all",
            "{",
            "[1, 2, 3]",  # valid JSON, wrong shape
            '"a bare string"',
            "null",
            reply(trades=[]),  # no message key
            reply(message=""),
            reply(message="   "),
            reply(message=42),
            reply(message=None),
        ],
    )
    def test_a_reply_with_no_usable_message_raises(self, raw):
        """The only failure mode that loses the turn. Everything else degrades
        to a rejection, because the message is what the user actually reads."""
        with pytest.raises(MalformedReplyError):
            parse_reply(raw)


class TestTrades:
    def test_a_valid_trade_survives(self):
        parsed = parse_reply(
            reply(message="Buying.", trades=[{"ticker": "AAPL", "side": "buy", "quantity": 10}])
        )

        (trade,) = parsed.trades
        assert (trade.ticker, trade.side, trade.quantity) == ("AAPL", "buy", 10.0)
        assert parsed.rejected == []

    @pytest.mark.parametrize(
        "trade",
        [
            {"ticker": "AAPL", "side": "buy", "quantity": 0},
            {"ticker": "AAPL", "side": "buy", "quantity": -5},
            {"ticker": "AAPL", "side": "buy", "quantity": "ten"},
            {"ticker": "AAPL", "side": "buy", "quantity": None},
            {"ticker": "AAPL", "side": "short", "quantity": 1},
            {"ticker": "AAPL", "side": "BUY", "quantity": 1},  # Literal is exact
            {"ticker": "", "side": "buy", "quantity": 1},
            {"ticker": "NOT A TICKER", "side": "buy", "quantity": 1},
            {"ticker": "../../etc/passwd", "side": "buy", "quantity": 1},
            {"ticker": "AAPL", "side": "buy"},
            {"ticker": "AAPL", "quantity": 1},
            {"side": "buy", "quantity": 1},
            {"ticker": "AAPL", "side": "buy", "quantity": 1, "price": 0.01},  # extra="forbid"
            "buy 10 AAPL",
            None,
            42,
            [],
        ],
    )
    def test_a_bad_trade_is_rejected_not_executed(self, trade):
        parsed = parse_reply(reply(message="Trying.", trades=[trade]))

        assert parsed.trades == []
        (rejection,) = parsed.rejected
        assert rejection.kind == "trade"
        assert rejection.reason

    def test_a_client_named_price_cannot_ride_along(self):
        """PLAN.md §8: a trade never accepts a price from the client, and the
        model is a client. `extra="forbid"` is what makes naming one a rejected
        action rather than a field quietly ignored."""
        parsed = parse_reply(
            reply(
                message="Buying cheap.",
                trades=[{"ticker": "AAPL", "side": "buy", "quantity": 1, "price": 0.01}],
            )
        )

        assert parsed.trades == []
        assert "price" in parsed.rejected[0].reason

    @pytest.mark.parametrize("quantity", [float("inf"), float("-inf"), float("nan")])
    def test_non_finite_quantities_are_rejected(self, quantity):
        """`inf > 0` is True, so `gt=0` alone would admit an infinite order.
        JSON has no literal for these, but `Infinity` is what json.dumps emits
        and json.loads accepts."""
        parsed = parse_reply(
            reply(message="?", trades=[{"ticker": "AAPL", "side": "buy", "quantity": quantity}])
        )

        assert parsed.trades == []
        assert parsed.rejected

    def test_good_trades_survive_alongside_a_bad_one(self):
        """The whole reason parsing is per-item: one broken action must not
        discard the message and the trades that were fine."""
        parsed = parse_reply(
            reply(
                message="Two of three.",
                trades=[
                    {"ticker": "AAPL", "side": "buy", "quantity": 1},
                    {"ticker": "!!", "side": "buy", "quantity": 1},
                    {"ticker": "MSFT", "side": "sell", "quantity": 2},
                ],
            )
        )

        assert [t.ticker for t in parsed.trades] == ["AAPL", "MSFT"]
        assert len(parsed.rejected) == 1
        assert parsed.message == "Two of three."

    def test_trades_beyond_the_cap_are_rejected_and_reported(self):
        over = MAX_TRADES_PER_REPLY + 3
        parsed = parse_reply(
            reply(
                message="Rebalancing.",
                trades=[{"ticker": "AAPL", "side": "buy", "quantity": 1}] * over,
            )
        )

        assert len(parsed.trades) == MAX_TRADES_PER_REPLY
        assert str(MAX_TRADES_PER_REPLY) in parsed.rejected[0].reason

    def test_the_whole_overflow_is_one_rejection_not_one_each(self):
        """Twenty identical "over the cap" lines in the reply and in the stored
        transcript would bury whatever else went wrong on that turn."""
        parsed = parse_reply(
            reply(
                message="Rebalancing.",
                trades=[{"ticker": "AAPL", "side": "buy", "quantity": 1}]
                * (MAX_TRADES_PER_REPLY + 20),
            )
        )

        assert len(parsed.rejected) == 1
        assert "20" in parsed.rejected[0].reason
        assert "19 more" in parsed.rejected[0].excerpt

    def test_a_single_over_cap_item_says_so_without_a_count(self):
        parsed = parse_reply(
            reply(
                message="?",
                trades=[{"ticker": "AAPL", "side": "buy", "quantity": 1}]
                * (MAX_TRADES_PER_REPLY + 1),
            )
        )

        assert len(parsed.rejected) == 1
        assert "more)" not in parsed.rejected[0].excerpt

    def test_over_cap_and_invalid_items_are_both_reported(self):
        """Two different things went wrong; collapsing them would hide one."""
        parsed = parse_reply(
            reply(
                message="?",
                trades=[{"ticker": "!!", "side": "buy", "quantity": 1}]
                + [{"ticker": "AAPL", "side": "buy", "quantity": 1}] * (MAX_TRADES_PER_REPLY + 2),
            )
        )

        assert len(parsed.trades) == MAX_TRADES_PER_REPLY
        assert len(parsed.rejected) == 2

    def test_a_non_list_trades_field_is_one_rejection(self):
        parsed = parse_reply(reply(message="?", trades={"ticker": "AAPL"}))

        assert parsed.trades == []
        assert parsed.rejected[0].reason.startswith("Expected a list")

    def test_a_missing_trades_field_is_not_an_error(self):
        assert parse_reply(reply(message="Just talking.")).rejected == []


class TestWatchlistChanges:
    def test_a_valid_change_survives(self):
        parsed = parse_reply(
            reply(message="Watching.", watchlist_changes=[{"ticker": "PYPL", "action": "add"}])
        )

        (change,) = parsed.watchlist_changes
        assert (change.ticker, change.action) == ("PYPL", "add")

    @pytest.mark.parametrize(
        "change",
        [
            {"ticker": "PYPL", "action": "delete"},
            {"ticker": "PYPL"},
            {"action": "add"},
            {"ticker": "a b", "action": "add"},
            {"ticker": "PYPL", "action": "add", "user_id": "someone-else"},
            None,
        ],
    )
    def test_a_bad_change_is_rejected(self, change):
        parsed = parse_reply(reply(message="?", watchlist_changes=[change]))

        assert parsed.watchlist_changes == []
        assert parsed.rejected[0].kind == "watchlist"

    def test_changes_beyond_the_cap_are_rejected(self):
        over = MAX_WATCHLIST_CHANGES_PER_REPLY + 1
        parsed = parse_reply(
            reply(message="?", watchlist_changes=[{"ticker": "PYPL", "action": "add"}] * over)
        )

        assert len(parsed.watchlist_changes) == MAX_WATCHLIST_CHANGES_PER_REPLY
        assert len(parsed.rejected) == 1
        assert "watchlist" in parsed.rejected[0].reason


class TestRejectionReporting:
    def test_a_long_item_is_truncated_in_the_excerpt(self):
        """The excerpt goes into a chat bubble and into `chat_messages`. Model
        output is not bounded by anything the parser controls, so it is cut
        here rather than at the database."""
        parsed = parse_reply(reply(message="?", trades=[{"ticker": "A" * 5000}]))

        assert len(parsed.rejected[0].excerpt) < 200
        assert parsed.rejected[0].excerpt.endswith("…")

    def test_the_reason_names_the_offending_field(self):
        parsed = parse_reply(
            reply(message="?", trades=[{"ticker": "AAPL", "side": "buy", "quantity": -1}])
        )

        assert "quantity" in parsed.rejected[0].reason

    def test_trade_and_watchlist_rejections_are_both_reported(self):
        parsed = parse_reply(reply(message="?", trades=["bad"], watchlist_changes=["also bad"]))

        assert {r.kind for r in parsed.rejected} == {"trade", "watchlist"}


class TestWireSchema:
    def test_the_response_format_schema_matches_the_plan(self):
        """PLAN.md §9 fixes these three keys. The schema is what the provider
        is handed, so a rename here silently changes what the model returns."""
        assert set(AssistantReply.model_json_schema()["properties"]) == {
            "message",
            "trades",
            "watchlist_changes",
        }

    def test_every_property_is_required(self):
        """Structured outputs are most portable when nothing is optional; the
        parser tolerates omissions anyway."""
        schema = AssistantReply.model_json_schema()
        assert set(schema["required"]) == set(schema["properties"])

    def test_a_reply_the_wire_schema_accepts_is_one_the_parser_accepts(self):
        """The two must not drift: the model is told to produce the wire schema,
        so anything valid under it has to survive parsing intact."""
        wire = AssistantReply(
            message="Bought.",
            trades=[{"ticker": "AAPL", "side": "buy", "quantity": 1.5}],
            watchlist_changes=[{"ticker": "PYPL", "action": "add"}],
        )

        parsed = parse_reply(wire.model_dump_json())

        assert parsed.message == "Bought."
        assert parsed.trades[0].quantity == 1.5
        assert parsed.watchlist_changes[0].ticker == "PYPL"
        assert parsed.rejected == []


class TestWireSchemaCompatibility:
    """The schema is sent to Cerebras, which refuses some JSON Schema keywords.

    OpenRouter's `provider.order` is a preference, not a pin, so a refusal does
    not fail — it silently reroutes to another host. Every call still succeeds,
    which is why this was invisible until a live run reported
    `provider='CoreWeave'`. These tests are the standing guard, because the next
    such drift would be just as quiet.
    """

    def keywords(self, node) -> set:
        if isinstance(node, dict):
            return set(node) | {k for v in node.values() for k in self.keywords(v)}
        if isinstance(node, list):
            return {k for v in node for k in self.keywords(v)}
        return set()

    def test_the_wire_schema_avoids_what_cerebras_rejects(self):
        """With `pattern` present Cerebras answers "Invalid fields for schema
        with types ['string']" and the request lands on a fallback provider at
        none of the speed PLAN.md §9 chose Cerebras for."""
        assert self.keywords(wire_schema()) & _UNSUPPORTED_SCHEMA_KEYWORDS == set()

    def test_the_model_still_declares_the_pattern(self):
        """Only the *wire* copy drops it. The provider was never the authority
        on what a ticker is — `LLMTrade` is, and it still rejects one."""
        assert "pattern" in self.keywords(AssistantReply.model_json_schema())

    def test_dropping_the_keyword_does_not_weaken_validation(self):
        """The point of the whole arrangement: a ticker the regex rejects is
        still a rejected action, whether or not the provider enforced it."""
        parsed = parse_reply(
            reply(message="?", trades=[{"ticker": "NOT A TICKER", "side": "buy", "quantity": 1}])
        )

        assert parsed.trades == []
        assert parsed.rejected

    def test_the_wire_schema_keeps_the_parts_the_provider_needs(self):
        schema = wire_schema()

        assert set(schema["required"]) == {"message", "trades", "watchlist_changes"}
        assert schema["additionalProperties"] is False
        # $defs/$ref survive: Cerebras accepts them, and inlining by hand would
        # be a second definition of the action shapes.
        assert "$defs" in schema

    def test_it_is_derived_from_the_model_not_written_out_again(self):
        """A hand-maintained copy would drift from what the parser enforces."""
        assert wire_schema()["properties"].keys() == (
            AssistantReply.model_json_schema()["properties"].keys()
        )
