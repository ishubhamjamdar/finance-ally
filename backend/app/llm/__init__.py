"""LLM integration — PLAN.md §9.

Import from here, never from a submodule; the same contract `app.market` and
`app.db` keep.

    from app.llm import LLMUnavailableError, MalformedReplyError, build_messages, complete, parse_reply

The split, and why the two error types are not one: `client` reaches the
provider, `schema` decides what came back means, `prompt` decides what was
asked, `mock` stands in for the provider. A provider that cannot be reached is
a 503 and a retry; a provider that answered badly is a conversational failure
the user should see as a reply. Collapsing them would make a missing API key
look like a confused model.
"""

from .client import (
    MAX_OUTPUT_TOKENS,
    MODEL,
    REQUEST_TIMEOUT_SECONDS,
    RESPONSE_FORMAT,
    LLMUnavailableError,
    complete,
    is_mock_enabled,
)
from .prompt import MAX_HISTORY_MESSAGES, SYSTEM_PROMPT, build_messages, render_context
from .schema import (
    MAX_TRADES_PER_REPLY,
    MAX_WATCHLIST_CHANGES_PER_REPLY,
    AssistantReply,
    LLMTrade,
    LLMWatchlistChange,
    MalformedReplyError,
    ParsedReply,
    RejectedAction,
    parse_reply,
    wire_schema,
)

__all__ = [
    "MAX_HISTORY_MESSAGES",
    "MAX_OUTPUT_TOKENS",
    "MAX_TRADES_PER_REPLY",
    "MAX_WATCHLIST_CHANGES_PER_REPLY",
    "MODEL",
    "REQUEST_TIMEOUT_SECONDS",
    "RESPONSE_FORMAT",
    "SYSTEM_PROMPT",
    "AssistantReply",
    "LLMTrade",
    "LLMUnavailableError",
    "LLMWatchlistChange",
    "MalformedReplyError",
    "ParsedReply",
    "RejectedAction",
    "build_messages",
    "complete",
    "is_mock_enabled",
    "parse_reply",
    "render_context",
    "wire_schema",
]
