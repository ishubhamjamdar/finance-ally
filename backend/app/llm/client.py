"""The call to the model — LiteLLM → OpenRouter → Cerebras (PLAN.md §9).

One function, `complete()`, which returns the model's raw text. Parsing is
`app.llm.schema`'s job and executing anything is `app.chat`'s, so this module
knows nothing about trades.

`LLM_MOCK=true` short-circuits to `app.llm.mock` here rather than at the
endpoint, so mock and live runs go through the *same* parse, the same
validation and the same execution path. A mock wired in higher up would let the
E2E suite pass against a response shape the real parser would reject.
"""

from __future__ import annotations

import logging
import os

from litellm import completion

from app.llm.mock import mock_completion
from app.llm.schema import AssistantReply

logger = logging.getLogger(__name__)

#: PLAN.md §9 fixes all three: the model, the provider routing, and structured
#: outputs. `order: [cerebras]` is what pins inference to Cerebras rather than
#: whichever OpenRouter host is cheapest this minute.
MODEL = "openrouter/openai/gpt-oss-120b"
EXTRA_BODY = {"provider": {"order": ["cerebras"]}}

#: Cerebras answers in low single-digit seconds, which is why §9 chose a single
#: complete response over token streaming. The timeout is generous against that
#: figure and still well short of a browser giving up, so a wedged provider
#: costs one request rather than a hung connection and an occupied worker.
REQUEST_TIMEOUT_SECONDS = 45.0

#: Enough for a paragraph of analysis and ten actions; short enough that a
#: model looping on a token cannot return a megabyte into a chat bubble.
MAX_OUTPUT_TOKENS = 1500

#: §9 asks for structured extraction, not deliberation, and Cerebras' speed is
#: the point of the choice. Raising this trades the responsiveness for depth
#: the trading assistant does not need.
REASONING_EFFORT = "low"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


class LLMUnavailableError(RuntimeError):
    """The model could not be reached, or is not configured.

    Distinct from `MalformedReplyError`, which means it answered badly. This
    one is the endpoint's 503: nothing about the conversation was wrong, so
    retrying the same message is the right move, and the turn is not written to
    history.
    """


def is_mock_enabled() -> bool:
    """Whether `LLM_MOCK` selects the deterministic mock (PLAN.md §5).

    Read at call time, not at import, so a test can flip it with `monkeypatch`
    — the same rule `DB_PATH` and `MASSIVE_API_KEY` follow.
    """
    return os.environ.get("LLM_MOCK", "").strip().lower() in _TRUE_VALUES


def complete(messages: list[dict[str, str]]) -> str:
    """Send one request and return the model's raw response text.

    Blocking — LiteLLM's `completion` is synchronous. Callers on the event loop
    must push it to a thread, or the simulator tick and every open SSE stream
    stall for the duration of the request.
    """
    if is_mock_enabled():
        return mock_completion(messages)

    # Read per call rather than at import so that `.env` loading order cannot
    # matter, and so a key added without a restart is picked up.
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise LLMUnavailableError(
            "The AI assistant is not configured: OPENROUTER_API_KEY is not set. "
            "Set it in .env, or set LLM_MOCK=true to use canned responses."
        )

    try:
        response = completion(
            model=MODEL,
            messages=messages,
            response_format=AssistantReply,
            reasoning_effort=REASONING_EFFORT,
            extra_body=EXTRA_BODY,
            timeout=REQUEST_TIMEOUT_SECONDS,
            max_tokens=MAX_OUTPUT_TOKENS,
            api_key=api_key,
        )
    except Exception as exc:
        # Logged in full server-side, summarised to the caller. Provider errors
        # quote the failing request back, and that text ends up in a chat
        # bubble and in `chat_messages`; a class name cannot carry a key or a
        # prompt into either.
        logger.exception("LLM request failed")
        raise LLMUnavailableError(
            f"Could not reach the AI assistant ({type(exc).__name__}). Please try again."
        ) from exc

    return _content(response)


def _content(response: object) -> str:
    """The assistant text out of a LiteLLM response.

    Defensive about the shape because it is a third-party object, not ours: a
    provider returning no choices, or a message with `content: null` — which
    happens when a model spends its whole budget on reasoning tokens — must
    become a graceful "I could not answer that", not an `IndexError` or an
    `AttributeError` surfacing as a 500.
    """
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, KeyError, TypeError) as exc:
        logger.error("LLM response had an unexpected shape: %r", response)
        raise LLMUnavailableError(
            "The AI assistant returned an unreadable response. Please try again."
        ) from exc

    # Returned as-is when empty; `parse_reply` raises MalformedReplyError for
    # it, which is the graceful path rather than the 503 this module's errors
    # produce. The model did answer — it just answered with nothing.
    return content or ""
