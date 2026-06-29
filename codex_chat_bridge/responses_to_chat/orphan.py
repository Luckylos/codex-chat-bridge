"""Responses→Chat: orphan tool output detection.

When a function_call_output / custom_tool_call_output / tool_search_output
references a call_id that has no matching tool call in the current request
or session history, it must be downgraded to a user-role message to avoid
Chat Completions rejecting a tool message without a preceding assistant
message with tool_calls.
"""
from __future__ import annotations

from typing import Any

from ..models import ChatMessage


def has_matching_call(call_id: str, pending_tool_calls: list[dict[str, Any]], messages: list[ChatMessage]) -> bool:
    """Check if a call_id has a matching tool call in pending buffer or flushed messages.

    Args:
        call_id: The call_id from a tool output item.
        pending_tool_calls: Tool calls accumulated in the current dispatch loop.
        messages: Already-flushed messages in the chat history.

    Returns:
        True if a matching call_id is found.
    """
    if any(tc.get("id") == call_id or tc.get("call_id") == call_id for tc in pending_tool_calls):
        return True
    return any(
        msg.role == "assistant" and any(tc.get("id") == call_id for tc in (msg.tool_calls or []))
        for msg in messages
    )
