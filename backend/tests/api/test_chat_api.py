"""Tests for the chat endpoints — status codes and response shape.

The rules are tested in `tests/test_chat.py`; what is asserted here is the
translation. In particular the two failure modes that must *not* look alike: a
model that could not be reached is a 503, and a model that answered badly is a
200 whose message says so. Collapsing them would tell a user to retry a request
that will fail identically, or hide a dead provider behind a friendly sentence.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.api.schemas import MAX_CHAT_MESSAGE_CHARS
from app.chat import MALFORMED_REPLY_MESSAGE
from app.llm import LLMUnavailableError
from app.llm import complete as real_complete
from tests.conftest import stored_messages


@pytest.fixture(autouse=True)
def model(stub_model):
    """Every test in this module talks to the stub, never a provider."""
    return stub_model


class TestPostChat:
    def test_a_message_returns_the_documented_shape(self, client):
        response = client.post("/api/chat", json={"message": "hello"})

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"message", "actions", "portfolio"}
        assert body["portfolio"]["cash_balance"] == 10000.0
        assert body["actions"] == []

    def test_an_executed_trade_appears_in_actions_and_in_the_portfolio(self, client, model):
        model.replies(message="Bought.", trades=[{"ticker": "AAPL", "side": "buy", "quantity": 10}])

        body = client.post("/api/chat", json={"message": "buy 10 AAPL"}).json()

        (action,) = body["actions"]
        assert action["ok"] is True
        assert action["kind"] == "trade"
        assert action["ticker"] == "AAPL"
        assert action["result"]["quantity"] == 10
        assert body["portfolio"]["cash_balance"] == 8000.0

    def test_an_action_serialises_every_documented_field(self, client, model):
        model.replies(watchlist_changes=[{"ticker": "PYPL", "action": "add"}])

        (action,) = client.post("/api/chat", json={"message": "watch PYPL"}).json()["actions"]

        assert set(action) == {"kind", "ok", "summary", "detail", "ticker", "action", "result"}

    def test_a_refused_trade_is_a_200_with_the_reason(self, client, model):
        """Not a 400. The request was fine; one of the things the assistant
        tried to do was not, and the reply still has to reach the user."""
        model.replies(trades=[{"ticker": "AAPL", "side": "buy", "quantity": 10000}])

        response = client.post("/api/chat", json={"message": "buy everything"})

        assert response.status_code == 200
        (action,) = response.json()["actions"]
        assert action["ok"] is False
        assert "Insufficient cash" in action["detail"]

    def test_a_malformed_model_reply_is_a_200_never_a_500(self, client, model):
        """PLAN.md §Checkpoint 4, stated as an exit criterion."""
        model.replies_raw("this is not JSON")

        response = client.post("/api/chat", json={"message": "hello"})

        assert response.status_code == 200
        assert response.json()["message"] == MALFORMED_REPLY_MESSAGE

    def test_an_unreachable_provider_is_a_503(self, client, model):
        model.fails(LLMUnavailableError("OPENROUTER_API_KEY is not set"))

        response = client.post("/api/chat", json={"message": "hello"})

        assert response.status_code == 503
        assert "OPENROUTER_API_KEY" in response.json()["detail"]

    def test_no_market_source_is_a_503(self, sourceless_client):
        """The chat can trade, so it takes the same refusal `POST
        /api/portfolio/trade` takes: with a dead feed every price is frozen, and
        filling against them through the chat would be a way around it."""
        response = sourceless_client.post("/api/chat", json={"message": "hello"})

        assert response.status_code == 503
        assert "Market data" in response.json()["detail"]

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"message": ""},
            {"message": "   "},
            {"message": None},
            {"message": 42},
            {"message": "hi", "user_id": "someone-else"},
            {"message": "hi", "trades": [{"ticker": "AAPL"}]},
        ],
    )
    def test_a_malformed_request_is_a_422(self, client, payload):
        assert client.post("/api/chat", json=payload).status_code == 422

    def test_an_over_long_message_is_a_422(self, client):
        """The only free-text field in the API, and every character of it is
        forwarded to a paid provider inside a prompt carrying history too."""
        response = client.post("/api/chat", json={"message": "x" * (MAX_CHAT_MESSAGE_CHARS + 1)})

        assert response.status_code == 422

    def test_a_message_at_the_limit_is_accepted(self, client):
        response = client.post("/api/chat", json={"message": "x" * MAX_CHAT_MESSAGE_CHARS})

        assert response.status_code == 200

    def test_a_rejected_request_never_reaches_the_provider(self, client, model):
        client.post("/api/chat", json={"message": "   "})

        assert model.calls == 0


class TestChatHistory:
    def test_an_empty_conversation_returns_an_empty_list(self, client):
        response = client.get("/api/chat/history")

        assert response.status_code == 200
        assert response.json() == {"messages": []}

    def test_it_returns_both_turns_oldest_first(self, client, model):
        model.replies(message="Noted.")
        client.post("/api/chat", json={"message": "hello"})

        messages = client.get("/api/chat/history").json()["messages"]

        assert [(m["role"], m["content"]) for m in messages] == [
            ("user", "hello"),
            ("assistant", "Noted."),
        ]
        assert set(messages[0]) == {"id", "role", "content", "actions", "created_at"}

    def test_actions_come_back_decoded_not_as_a_json_string(self, client, model):
        model.replies(trades=[{"ticker": "AAPL", "side": "buy", "quantity": 1}])
        client.post("/api/chat", json={"message": "buy"})

        assistant = client.get("/api/chat/history").json()["messages"][-1]

        assert assistant["actions"][0]["ticker"] == "AAPL"

    def test_history_survives_a_new_client(self, client, model, app):
        """Checkpoint 7's "history survives a page reload", at the layer that
        decides it: the transcript is in SQLite, not in a process variable."""
        client.post("/api/chat", json={"message": "remember me"})

        messages = TestClient(app).get("/api/chat/history").json()["messages"]

        assert messages[0]["content"] == "remember me"

    @pytest.mark.parametrize("limit", [0, -1, 501, "abc"])
    def test_an_out_of_range_limit_is_a_422(self, client, limit):
        assert client.get("/api/chat/history", params={"limit": limit}).status_code == 422

    def test_the_limit_keeps_the_newest_turns(self, client, model):
        for n in range(3):
            model.replies(message=f"reply {n}")
            client.post("/api/chat", json={"message": f"message {n}"})

        messages = client.get("/api/chat/history", params={"limit": 2}).json()["messages"]

        assert [m["content"] for m in messages] == ["message 2", "reply 2"]


class TestMockMode:
    """`LLM_MOCK=true` end to end, which is how Checkpoint 9 will run."""

    @pytest.fixture
    def mock_client(self, app, mock_llm, monkeypatch):
        """A client using the real `complete()` in mock mode — the autouse stub
        is undone, so the request travels the production path end to end with
        only the provider itself replaced."""
        monkeypatch.setattr("app.chat.complete", real_complete)
        return TestClient(app)

    def test_a_mocked_trade_moves_cash_and_positions(self, mock_client, read_cash):
        """PLAN.md §Checkpoint 4's first exit criterion, over HTTP with no
        stubbing below the endpoint."""
        body = mock_client.post("/api/chat", json={"message": "buy 10 AAPL"}).json()

        assert body["actions"][0]["ok"] is True
        assert body["portfolio"]["cash_balance"] == 8000.0
        assert read_cash() == 8000.0

    def test_the_response_is_schema_valid(self, mock_client):
        body = mock_client.post("/api/chat", json={"message": "how am I doing?"}).json()

        assert isinstance(body["message"], str) and body["message"]
        assert isinstance(body["actions"], list)
        assert "total_value" in body["portfolio"]

    def test_a_mocked_watchlist_change_is_stored(self, mock_client):
        mock_client.post("/api/chat", json={"message": "add PYPL"})

        tickers = [row["ticker"] for row in mock_client.get("/api/watchlist").json()["tickers"]]
        assert "PYPL" in tickers

    def test_it_needs_no_api_key(self, mock_client):
        """`tests/conftest.no_llm_network` has already cleared the key, so this
        passing at all is the assertion — CI runs with no secrets."""
        assert mock_client.post("/api/chat", json={"message": "hello"}).status_code == 200

    def test_the_exchange_is_persisted(self, mock_client):
        mock_client.post("/api/chat", json={"message": "buy 1 AAPL"})

        roles = [role for role, _, _ in stored_messages()]
        assert roles == ["user", "assistant"]
        assert json.loads(stored_messages()[1][2])[0]["ok"] is True
