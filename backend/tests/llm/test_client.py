"""Tests for `app.llm.client` — transport, configuration and failure.

No test here reaches the network. `tests/conftest.no_llm_network` clears both
`OPENROUTER_API_KEY` and `LLM_MOCK` for every test in the suite, so the only
way to a live call is to set them back deliberately, and nothing does.
"""

from __future__ import annotations

import json

import pytest

from app.llm import MODEL, LLMUnavailableError, complete, is_mock_enabled
from app.llm.client import (
    EXTRA_BODY,
    MAX_OUTPUT_TOKENS,
    REASONING_EFFORT,
    REQUEST_TIMEOUT_SECONDS,
)
from app.llm.schema import AssistantReply

MESSAGES = [{"role": "user", "content": "hello"}]


class StubResponse:
    """The shape LiteLLM returns, built by hand.

    Not a `MagicMock`: Checkpoint 1 lost thirteen tests to a mock that
    fabricated whatever attribute the code asked for, including the one that
    did not exist. A hand-built stub fails when the access path changes, which
    is the entire point of testing the access path.
    """

    def __init__(self, content):
        message = type("Message", (), {"content": content})()
        choice = type("Choice", (), {"message": message})()
        self.choices = [choice]


@pytest.fixture
def recorded_call(monkeypatch):
    """Capture the kwargs `complete()` sends, returning a canned reply."""
    calls = []

    def fake_completion(**kwargs):
        calls.append(kwargs)
        return StubResponse(json.dumps({"message": "ok", "trades": [], "watchlist_changes": []}))

    monkeypatch.setattr("app.llm.client.completion", fake_completion)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
    return calls


class TestMockSelection:
    @pytest.mark.parametrize("value", ["true", "TRUE", "True", "1", "yes", "on", " true "])
    def test_truthy_values_enable_the_mock(self, monkeypatch, value):
        monkeypatch.setenv("LLM_MOCK", value)
        assert is_mock_enabled() is True

    @pytest.mark.parametrize("value", ["false", "0", "no", "", "  ", "off", "maybe"])
    def test_everything_else_leaves_it_live(self, monkeypatch, value):
        monkeypatch.setenv("LLM_MOCK", value)
        assert is_mock_enabled() is False

    def test_unset_leaves_it_live(self):
        assert is_mock_enabled() is False

    def test_the_mock_short_circuits_before_the_provider(self, monkeypatch):
        """Mock mode must not need a key, and must not reach LiteLLM at all —
        PLAN.md §9 wants CI runs with no secrets."""

        def explode(**kwargs):  # pragma: no cover - the point is that it never runs
            raise AssertionError("LiteLLM was called in mock mode")

        monkeypatch.setattr("app.llm.client.completion", explode)
        monkeypatch.setenv("LLM_MOCK", "true")

        assert json.loads(complete(MESSAGES))["message"]

    def test_the_mock_is_read_per_call_not_at_import(self, monkeypatch):
        """`LLM_MOCK` follows the same rule as `DB_PATH`: read at call time, so
        a test can flip it without reimporting the module."""
        assert is_mock_enabled() is False
        monkeypatch.setenv("LLM_MOCK", "true")
        assert is_mock_enabled() is True


class TestConfiguration:
    def test_a_missing_key_is_reported_not_attempted(self, monkeypatch):
        def explode(**kwargs):  # pragma: no cover - never reached
            raise AssertionError("called the provider with no key")

        monkeypatch.setattr("app.llm.client.completion", explode)

        with pytest.raises(LLMUnavailableError, match="OPENROUTER_API_KEY"):
            complete(MESSAGES)

    def test_a_blank_key_counts_as_missing(self, monkeypatch):
        """`.env` files routinely carry `OPENROUTER_API_KEY=` with nothing after
        it — the same case `MASSIVE_API_KEY` strips for."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "   ")

        with pytest.raises(LLMUnavailableError, match="not configured"):
            complete(MESSAGES)

    def test_the_error_says_how_to_fix_it(self, monkeypatch):
        with pytest.raises(LLMUnavailableError) as caught:
            complete(MESSAGES)

        assert "LLM_MOCK" in str(caught.value)

    def test_the_call_pins_model_provider_and_structured_output(self, recorded_call):
        """PLAN.md §9 fixes all three. Asserted because a silent change of
        provider routing or response format is invisible until a live run."""
        complete(MESSAGES)

        (kwargs,) = recorded_call
        assert kwargs["model"] == MODEL == "openrouter/openai/gpt-oss-120b"
        assert kwargs["extra_body"] == EXTRA_BODY == {"provider": {"order": ["cerebras"]}}
        assert kwargs["response_format"] is AssistantReply
        assert kwargs["reasoning_effort"] == REASONING_EFFORT
        assert kwargs["messages"] == MESSAGES

    def test_the_call_is_bounded_in_time_and_length(self, recorded_call):
        """Without both, one wedged provider holds a worker open indefinitely
        and one looping model returns a megabyte into a chat bubble."""
        complete(MESSAGES)

        (kwargs,) = recorded_call
        assert kwargs["timeout"] == REQUEST_TIMEOUT_SECONDS
        assert kwargs["max_tokens"] == MAX_OUTPUT_TOKENS

    def test_the_key_is_passed_explicitly(self, recorded_call):
        complete(MESSAGES)

        assert recorded_call[0]["api_key"] == "sk-or-test-key"


class TestFailure:
    def test_a_provider_error_becomes_llm_unavailable(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
        monkeypatch.setattr(
            "app.llm.client.completion",
            lambda **kwargs: (_ for _ in ()).throw(ConnectionError("upstream is down")),
        )

        with pytest.raises(LLMUnavailableError, match="ConnectionError"):
            complete(MESSAGES)

    def test_the_provider_error_text_is_not_forwarded_to_the_caller(self, monkeypatch):
        """The raised message is shown to the user and stored. Provider errors
        quote the failing request back — headers, prompt, sometimes the key —
        so only the exception's class name crosses this boundary."""
        secret = "sk-or-v1-deadbeefdeadbeefdeadbeef"
        monkeypatch.setenv("OPENROUTER_API_KEY", secret)
        monkeypatch.setattr(
            "app.llm.client.completion",
            lambda **kwargs: (_ for _ in ()).throw(
                RuntimeError(f"401 unauthorized for key {secret} on prompt 'buy 10 AAPL'")
            ),
        )

        with pytest.raises(LLMUnavailableError) as caught:
            complete(MESSAGES)

        assert secret not in str(caught.value)
        assert "buy 10 AAPL" not in str(caught.value)

    def test_a_response_with_no_choices_is_reported_not_raised_raw(self, monkeypatch):
        """An IndexError escaping here would be a 500. PLAN.md §Checkpoint 4
        rules that out for any model misbehaviour."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
        empty = type("Empty", (), {"choices": []})()
        monkeypatch.setattr("app.llm.client.completion", lambda **kwargs: empty)

        with pytest.raises(LLMUnavailableError, match="unreadable"):
            complete(MESSAGES)

    def test_a_response_missing_the_message_attribute_is_reported(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
        odd = type("Odd", (), {"choices": [object()]})()
        monkeypatch.setattr("app.llm.client.completion", lambda **kwargs: odd)

        with pytest.raises(LLMUnavailableError, match="unreadable"):
            complete(MESSAGES)

    def test_null_content_returns_empty_rather_than_raising(self, monkeypatch):
        """A model that spends its whole budget on reasoning tokens returns
        `content: null`. That is a bad *answer*, not an unreachable provider,
        so it must reach `parse_reply` and become the graceful message — a 503
        would tell the user to retry a request that will fail the same way."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
        monkeypatch.setattr("app.llm.client.completion", lambda **kwargs: StubResponse(None))

        assert complete(MESSAGES) == ""

    def test_content_is_returned_verbatim(self, monkeypatch):
        """Parsing belongs to `app.llm.schema`; this module must not tidy,
        strip or repair what the model said."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
        monkeypatch.setattr(
            "app.llm.client.completion", lambda **kwargs: StubResponse("  {not json}  ")
        )

        assert complete(MESSAGES) == "  {not json}  "
