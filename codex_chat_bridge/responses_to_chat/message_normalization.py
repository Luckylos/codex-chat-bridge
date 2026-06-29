from __future__ import annotations

from typing import Any

from ..models import ChatMessage
from .content_helpers import flatten_text_content


def _messages_are_mergeable(a: ChatMessage, b: ChatMessage) -> bool:
    """Whether two adjacent messages can be merged."""
    if a.role != b.role:
        return False
    return a.role in ("system",)


def _merge_content(a: Any, b: Any) -> str | list | None:
    """Merge two message content fields."""
    a_text = flatten_text_content(a).strip()
    b_text = flatten_text_content(b).strip()
    merged = "\n\n".join(p for p in [a_text, b_text] if p)
    return merged or None


def _merge_messages(a: ChatMessage, b: ChatMessage) -> ChatMessage:
    """Merge two adjacent same-role messages."""
    merged_content = _merge_content(a.content, b.content)
    return ChatMessage(role=a.role, content=merged_content)


def _sanitize_chat_messages(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Three-stage message normalization pipeline.

    1. Remove empty: filter out messages with no content and no tool_calls
    2. Merge: combine adjacent same-role messages (avoid upstream rejecting user→user)
    3. Role compliance: let upstream handle invalid roles
    """
    if not messages:
        return messages

    # Step 1: Remove empty — only filter completely blank assistant/tool messages
    sanitized: list[ChatMessage] = []
    for msg in messages:
        has_content = msg.content is not None and msg.content != "" and not (
            isinstance(msg.content, list) and not msg.content
        )
        has_tool_calls = bool(msg.tool_calls)
        has_tool_call_id = bool(msg.tool_call_id)
        # Keep user/system even if content is empty (upstream may need context placeholder)
        if msg.role in ("user", "system"):
            sanitized.append(msg)
        elif has_content or has_tool_calls or has_tool_call_id:
            sanitized.append(msg)
    if not sanitized:
        return sanitized

    # Step 2: Merge adjacent same-role messages
    merged: list[ChatMessage] = [sanitized[0]]
    for msg in sanitized[1:]:
        prev = merged[-1]
        if _messages_are_mergeable(prev, msg):
            merged[-1] = _merge_messages(prev, msg)
        else:
            merged.append(msg)

    # Step 3: role compliance — don't force insert placeholders, let upstream handle

    return merged


def collapse_system_messages_to_head(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Collect all system messages and place a single merged system message at the head."""
    system_chunks: list[str] = []
    rest: list[ChatMessage] = []
    for message in messages:
        if message.role == "system":
            text = flatten_text_content(message.content).strip()
            if text:
                system_chunks.append(text)
            continue
        rest.append(message)
    if not system_chunks:
        return rest
    return [ChatMessage(role="system", content="\n\n".join(system_chunks))] + rest
