from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, cast
from ..protocol.types import ChatToolCall, ResponsesInputItem

from ..bridge_context import BridgeToolContext, TOOL_SEARCH_PROXY_NAME, custom_tool_input_to_chat_arguments, canonical_json_string
from ..models import ChatMessage, ResponsesRequest
from ..tool_arguments import canonicalize_tool_arguments
from .content import reasoning_item_text, normalize_tool_output_content, _join_reasoning
from .content_mapping import (
    chat_message_content_from_response_content,
    iter_input_items,
)
from .media import chat_image_part_from_input_item, chat_audio_part_from_input_item
from .tools import (
    normalize_message_tool_calls,
    append_reasoning_to_last_assistant,
    ensure_tool_call_reasoning_content,
    backfill_tool_call_reasoning_content,
)
from .errors import UnsupportedResponsesInputItemError
from .orphan import has_matching_call

_logger = logging.getLogger("codex-chat-bridge")


def _existing_call_ids(messages: list[ChatMessage]) -> set[str]:
    """Scan messages for already-present call_ids (from tool_calls and tool_call_id).

    Used by append_input_items_as_chat_messages when continuing a session
    to skip duplicate function_call / function_call_output items.
    """
    ids: set[str] = set()
    for msg in messages:
        if msg.tool_call_id:
            ids.add(msg.tool_call_id)
        if msg.tool_calls:
            for tc in msg.tool_calls:
                cid = tc.get("id") or tc.get("call_id")
                if isinstance(cid, str) and cid:
                    ids.add(cid)
    return ids


def _extract_call_id(item: ResponsesInputItem) -> str:
    """Extract and normalise a call_id from an item dict."""
    return str(item.get("call_id") or item.get("id") or "call_0")


def _should_skip(item: ResponsesInputItem, skip_ids: set[str]) -> bool:
    """Return True if this item's call_id was already seen in the session."""
    cid = item.get("call_id") or item.get("id")
    return isinstance(cid, str) and cid in skip_ids


def flush_pending_tool_calls(
    messages: list[ChatMessage],
    pending_tool_calls: list[ChatToolCall],
    pending_reasoning: str | None,
) -> None:
    """Flush accumulated tool_calls into a single assistant message."""
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


def _merge_reasoning_content(existing: str | None, value: object) -> str | None:
    if not isinstance(value, str):
        return existing
    return _join_reasoning(existing, value)


def append_input_items_as_chat_messages(
    payload: ResponsesRequest,
    messages: list[ChatMessage],
    tool_context: BridgeToolContext,
) -> None:
    pending_tool_calls: list[ChatToolCall] = []
    pending_reasoning: str | None = None
    skip_call_ids = _existing_call_ids(messages)

    def _flush() -> None:
        nonlocal pending_reasoning
        flush_pending_tool_calls(messages, pending_tool_calls, pending_reasoning)
        pending_reasoning = None

    for item in iter_input_items(payload):
        # ---- Plain string input → user message ----
        if isinstance(item, str):
            _flush()
            messages.append(ChatMessage(role="user", content=item))
            continue

        if not isinstance(item, dict):
            _flush()
            _logger.debug("Skipping non-dict responses input item: %r", item)
            continue

        item_type = item.get("type")

        # ---- Reasoning items ----
        if item_type == "reasoning":
            text = reasoning_item_text(item).strip()
            if text:
                if not pending_tool_calls and append_reasoning_to_last_assistant(messages, text):
                    continue
                pending_reasoning = text if not pending_reasoning else pending_reasoning + "\n\n" + text
            continue

        # ---- Text content items → user message ----
        if item_type in {"input_text", "output_text", "text", "latest_reminder"}:
            text = item.get("text", "") if isinstance(item.get("text"), str) else ""
            _flush()
            messages.append(ChatMessage(role="user", content=text))
            continue

        # ---- Image items → user message with image_url part ----
        if item_type == "input_image":
            _flush()
            try:
                image_part = chat_image_part_from_input_item(item)
            except UnsupportedResponsesInputItemError as exc:
                _logger.debug("Skipping unsupported responses %s item: %s", item_type, exc)
                continue
            messages.append(ChatMessage(role="user", content=[image_part]))
            continue

        # ---- Audio items → user message with input_audio part ----
        if item_type == "input_audio":
            _flush()
            try:
                audio_part = chat_audio_part_from_input_item(item)
            except UnsupportedResponsesInputItemError as exc:
                _logger.debug("Skipping unsupported responses %s item: %s", item_type, exc)
                continue
            messages.append(ChatMessage(role="user", content=[audio_part]))
            continue

        # ---- Tool call items → accumulate into pending_tool_calls ----
        if item_type == "function_call":
            if _should_skip(item, skip_call_ids):
                continue
            pending_reasoning = _merge_reasoning_content(
                pending_reasoning, item.get("reasoning_content")
            )
            pending_tool_calls.append({
                "id": item.get("call_id") or item.get("id") or "call_0",
                "type": "function",
                "function": {
                    "name": tool_context.chat_name_for_function(
                        item.get("name") or "unknown_tool",
                        item.get("namespace") if isinstance(item.get("namespace"), str) else None,
                    ),
                    "arguments": canonicalize_tool_arguments(item.get("arguments")),
                },
            })
            continue

        if item_type == "custom_tool_call":
            if _should_skip(item, skip_call_ids):
                continue
            pending_reasoning = _merge_reasoning_content(
                pending_reasoning, item.get("reasoning_content")
            )
            pending_tool_calls.append({
                "id": item.get("call_id") or item.get("id") or "call_0",
                "type": "function",
                "function": {
                    "name": item.get("name") or "unknown_tool",
                    "arguments": custom_tool_input_to_chat_arguments(item.get("input", "")),
                },
            })
            continue

        if item_type == "tool_search_call":
            if _should_skip(item, skip_call_ids):
                continue
            pending_reasoning = _merge_reasoning_content(
                pending_reasoning, item.get("reasoning_content")
            )
            # tool_search_call arguments is always a dict (e.g. {"query":...,"limit":5}),
            # not a JSON string.  canonicalize_tool_arguments handles this correctly
            # (dict reaches the json.dumps branch on line 30), but the naming is
            # misleading — it was designed for function_call string arguments.
            pending_tool_calls.append({
                "id": item.get("call_id") or item.get("id") or "call_0",
                "type": "function",
                "function": {
                    "name": TOOL_SEARCH_PROXY_NAME,
                    "arguments": canonicalize_tool_arguments(item.get("arguments")),
                },
            })
            continue

        # ---- Tool output items → tool message ----
        if item_type in {"function_call_output", "custom_tool_call_output", "tool_search_output"}:
            call_id = _extract_call_id(item)
            if _should_skip(item, skip_call_ids):
                continue
            _flush()
            if item_type == "function_call_output":
                tool_content = normalize_tool_output_content(item.get("output"))
            else:
                tool_content = canonical_json_string(item)
            # Check if this tool output has a matching tool call in the pending buffer
            # or has already been flushed. If not, it's an orphan — downgrade to user
            # message to avoid Chat Completions rejecting a tool message without a
            # preceding assistant message with tool_calls.
            if has_matching_call(call_id, pending_tool_calls, messages):
                messages.append(
                    ChatMessage(
                        role="tool",
                        tool_call_id=call_id,
                        content=tool_content,
                    )
                )
            else:
                # Orphan tool output: wrap as user message so upstream doesn't reject
                messages.append(
                    ChatMessage(
                        role="user",
                        content=f"Function call output ({call_id}): {tool_content}",
                    )
                )
            continue

        # ---- Generic message items (role/content dicts) ----
        if "role" in item or "content" in item or item_type == "message":
            _flush()
            role = str(item.get("role") or "user")
            chat_role = "system" if role in {"system", "developer"} else role
            if chat_role not in {"system", "user", "assistant", "tool"}:
                chat_role = "user"
            tool_call_id = str(item.get("tool_call_id")) if item.get("tool_call_id") is not None else None
            tool_calls = normalize_message_tool_calls(item.get("tool_calls"), tool_context) if chat_role == "assistant" else None
            reasoning_content = item.get("reasoning_content") if isinstance(item.get("reasoning_content"), str) else None
            messages.append(
                ChatMessage(
                    role=cast(Literal["system", "user", "assistant", "tool"], chat_role),
                    content=chat_message_content_from_response_content(item.get("content")),
                    tool_calls=tool_calls,
                    tool_call_id=tool_call_id,
                    reasoning_content=reasoning_content,
                )
            )
            ensure_tool_call_reasoning_content(messages[-1])
            continue

        # ---- Unknown item type → permissively skip, but log for visibility ----
        _flush()
        _logger.debug("Skipping unsupported responses input item type: %r", item_type)

    flush_pending_tool_calls(messages, pending_tool_calls, pending_reasoning)
    backfill_tool_call_reasoning_content(messages)
