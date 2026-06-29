from __future__ import annotations

from typing import Any

from ..bridge_context import BridgeToolContext
from ..models import ChatMessage
from ..response_semantics import canonicalize_tool_arguments
from .errors import UnsupportedResponsesInputItemError


def normalize_message_tool_calls(value: Any, tool_context: BridgeToolContext) -> list[dict[str, Any]] | None:
    """Normalize Responses tool_calls to Chat Completions format."""
    if not isinstance(value, list):
        return None
    normalized: list[dict[str, Any]] = []
    for index, tool_call in enumerate(value):
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else tool_call
        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        namespace = function.get("namespace") if isinstance(function.get("namespace"), str) else None
        normalized.append(
            {
                "id": str(tool_call.get("id") or tool_call.get("call_id") or f"call_{index}"),
                "type": "function",
                "function": {
                    "name": tool_context.chat_name_for_function(name, namespace),
                    "arguments": canonicalize_tool_arguments(function.get("arguments")),
                },
            }
        )
    return normalized or None


def message_has_tool_calls(message: ChatMessage) -> bool:
    return bool(message.role == "assistant" and message.tool_calls)


def append_reasoning_to_last_assistant(messages: list[ChatMessage], reasoning: str) -> bool:
    """Append reasoning text to the last assistant message in the list."""
    reasoning = reasoning.strip()
    if not reasoning:
        return False
    for message in reversed(messages):
        if message.role != "assistant":
            continue
        if message.reasoning_content and message.reasoning_content.strip():
            message.reasoning_content = message.reasoning_content.strip() + "\n\n" + reasoning
            return True
        message.reasoning_content = reasoning
        return True
    return False


def ensure_tool_call_reasoning_content(message: ChatMessage) -> None:
    """Backfill a placeholder reasoning_content if an assistant message has
    tool_calls but no reasoning — required by some upstream providers."""
    if message_has_tool_calls(message) and not (message.reasoning_content and message.reasoning_content.strip()):
        message.reasoning_content = "tool call"


def backfill_tool_call_reasoning_content(messages: list[ChatMessage]) -> None:
    """Ensure all assistant messages with tool_calls have reasoning_content."""
    for message in messages:
        ensure_tool_call_reasoning_content(message)
