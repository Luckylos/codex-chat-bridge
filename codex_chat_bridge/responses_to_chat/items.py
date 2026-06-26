from __future__ import annotations

from typing import Any

from ..bridge_context import BridgeToolContext, TOOL_SEARCH_PROXY_NAME, custom_tool_input_to_chat_arguments
from ..models import ChatMessage, ResponsesRequest
from ..response_semantics import canonicalize_tool_arguments
from .common import (
    append_reasoning_to_last_assistant,
    backfill_tool_call_reasoning_content,
    canonical_json_string,
    chat_image_part_from_input_item,
    chat_message_content_from_response_content,
    ensure_tool_call_reasoning_content,
    normalize_message_tool_calls,
    normalize_tool_output_content,
    reasoning_item_text,
    iter_input_items,
)
from .errors import UnsupportedResponsesInputItemError


def flush_pending_tool_calls(messages: list[ChatMessage], pending_tool_calls: list[dict[str, Any]], pending_reasoning: str | None) -> None:
    if not pending_tool_calls:
        return
    messages.append(
        ChatMessage(
            role="assistant",
            content=None,
            tool_calls=list(pending_tool_calls),
            reasoning_content=pending_reasoning or None,
        )
    )
    pending_tool_calls.clear()


def append_input_items_as_chat_messages(payload: ResponsesRequest, messages: list[ChatMessage], tool_context: BridgeToolContext) -> None:
    pending_tool_calls: list[dict[str, Any]] = []
    pending_reasoning: str | None = None

    for item in iter_input_items(payload):
        if isinstance(item, str):
            flush_pending_tool_calls(messages, pending_tool_calls, pending_reasoning)
            pending_reasoning = None
            messages.append(ChatMessage(role="user", content=item))
            continue
        if not isinstance(item, dict):
            flush_pending_tool_calls(messages, pending_tool_calls, pending_reasoning)
            pending_reasoning = None
            continue

        item_type = item.get("type")
        if item_type == "reasoning":
            text = reasoning_item_text(item).strip()
            if text:
                if not pending_tool_calls and append_reasoning_to_last_assistant(messages, text):
                    continue
                pending_reasoning = text if not pending_reasoning else pending_reasoning + "\n\n" + text
            continue
        if item_type in {"input_text", "output_text", "text"}:
            text = item.get("text", "") if isinstance(item.get("text"), str) else ""
            flush_pending_tool_calls(messages, pending_tool_calls, pending_reasoning)
            pending_reasoning = None
            messages.append(ChatMessage(role="user", content=text.strip()))
            continue
        if item_type == "input_image":
            flush_pending_tool_calls(messages, pending_tool_calls, pending_reasoning)
            pending_reasoning = None
            try:
                image_part = chat_image_part_from_input_item(item)
            except UnsupportedResponsesInputItemError:
                continue
            messages.append(ChatMessage(role="user", content=[image_part]))
            continue
        if item_type == "function_call":
            pending_tool_calls.append(
                {
                    "id": item.get("call_id") or item.get("id") or "call_0",
                    "type": "function",
                    "function": {
                        "name": tool_context.chat_name_for_function(item.get("name") or "unknown_tool", item.get("namespace") if isinstance(item.get("namespace"), str) else None),
                        "arguments": canonicalize_tool_arguments(item.get("arguments")),
                    },
                }
            )
            continue
        if item_type == "custom_tool_call":
            pending_tool_calls.append(
                {
                    "id": item.get("call_id") or item.get("id") or "call_0",
                    "type": "function",
                    "function": {
                        "name": item.get("name") or "unknown_tool",
                        "arguments": custom_tool_input_to_chat_arguments(item.get("input", "")),
                    },
                }
            )
            continue
        if item_type == "tool_search_call":
            pending_tool_calls.append(
                {
                    "id": item.get("call_id") or item.get("id") or "call_0",
                    "type": "function",
                    "function": {
                        "name": TOOL_SEARCH_PROXY_NAME,
                        "arguments": canonicalize_tool_arguments(item.get("arguments")),
                    },
                }
            )
            continue
        if item_type == "function_call_output":
            flush_pending_tool_calls(messages, pending_tool_calls, pending_reasoning)
            pending_reasoning = None
            messages.append(
                ChatMessage(
                    role="tool",
                    tool_call_id=str(item.get("call_id") or item.get("id") or "call_0"),
                    content=normalize_tool_output_content(item.get("output")),
                )
            )
            continue
        if item_type == "custom_tool_call_output":
            flush_pending_tool_calls(messages, pending_tool_calls, pending_reasoning)
            pending_reasoning = None
            messages.append(
                ChatMessage(
                    role="tool",
                    tool_call_id=str(item.get("call_id") or item.get("id") or "call_0"),
                    content=canonical_json_string(item),
                )
            )
            continue
        if item_type == "tool_search_output":
            flush_pending_tool_calls(messages, pending_tool_calls, pending_reasoning)
            pending_reasoning = None
            messages.append(
                ChatMessage(
                    role="tool",
                    tool_call_id=str(item.get("call_id") or item.get("id") or "call_0"),
                    content=canonical_json_string(item),
                )
            )
            continue

        if "role" in item or "content" in item or item_type == "message":
            flush_pending_tool_calls(messages, pending_tool_calls, pending_reasoning)
            role = str(item.get("role") or "user")
            chat_role = "system" if role in {"system", "developer"} else role
            if chat_role not in {"system", "user", "assistant", "tool"}:
                chat_role = "user"
            tool_call_id = str(item.get("tool_call_id")) if item.get("tool_call_id") is not None else None
            tool_calls = normalize_message_tool_calls(item.get("tool_calls"), tool_context) if chat_role == "assistant" else None
            reasoning_content = item.get("reasoning_content") if isinstance(item.get("reasoning_content"), str) else None
            messages.append(
                ChatMessage(
                    role=chat_role,  # type: ignore[arg-type]
                    content=chat_message_content_from_response_content(item.get("content")),
                    tool_calls=tool_calls,
                    tool_call_id=tool_call_id,
                    reasoning_content=reasoning_content,
                )
            )
            ensure_tool_call_reasoning_content(messages[-1])
            pending_reasoning = None
            continue

        flush_pending_tool_calls(messages, pending_tool_calls, pending_reasoning)
        pending_reasoning = None

    flush_pending_tool_calls(messages, pending_tool_calls, pending_reasoning)
    backfill_tool_call_reasoning_content(messages)
