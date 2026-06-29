from __future__ import annotations

from typing import Any

from ..bridge_context import BridgeToolContext, canonical_json_string, iter_request_input_items
from ..models import ChatMessage, ResponsesRequest
from .errors import UnsupportedResponsesInputItemError

# Re-export from split modules — all consumers continue to import from .common
from .content import (
    flatten_text_content,
    instruction_text,
    reasoning_item_text,
    normalize_tool_output_content,
)
from .media import (
    is_safe_image_url,
    chat_image_part_from_input_item,
    chat_audio_part_from_input_item,
)
from .message_normalization import (
    _sanitize_chat_messages,
    collapse_system_messages_to_head,
)
from .tools import (
    normalize_message_tool_calls,
    message_has_tool_calls,
    append_reasoning_to_last_assistant,
    ensure_tool_call_reasoning_content,
    backfill_tool_call_reasoning_content,
)

# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

EXTRA_CHAT_PASSTHROUGH_FIELDS = (
    "frequency_penalty",
    "logit_bias",
    "logprobs",
    "metadata",
    "n",
    "parallel_tool_calls",
    "presence_penalty",
    "response_format",
    "seed",
    "service_tier",
    "stop",
    "stream_options",
    "top_logprobs",
    "user",
)

BUILT_IN_RESPONSES_TOOLS = {
    "web_search",
    "web_search_preview",
    "file_search",
    "computer_use",
    "computer_use_preview",
    "code_interpreter",
    "image_generation",
    "mcp",
}


def is_openai_o_series(model: str | None) -> bool:
    if not isinstance(model, str):
        return False
    normalized = model.strip().lower()
    return normalized.startswith("o1") or normalized.startswith("o3") or normalized.startswith("o4")


def chat_message_content_from_response_content(content: Any) -> str | list[dict[str, Any]] | None:
    """Convert Responses content to Chat Completions message content."""
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return flatten_text_content(content)
    parts: list[dict[str, Any]] = []
    has_non_text = False
    for item in content:
        if isinstance(item, str):
            if item:
                parts.append({"type": "text", "text": item})
            continue
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type in {"input_text", "output_text", "text"} and isinstance(item.get("text"), str):
            parts.append({"type": "text", "text": item["text"]})
            continue
        if item_type == "refusal" and isinstance(item.get("refusal"), str):
            if item.get("refusal"):
                parts.append({"type": "text", "text": item["refusal"]})
            continue
        if item_type == "input_image":
            try:
                image_part = chat_image_part_from_input_item(item)
            except UnsupportedResponsesInputItemError:
                continue
            has_non_text = True
            parts.append(image_part)
            continue
        if item_type == "input_audio":
            # Responses input_audio → Chat input_audio part (with SSRF safety check)
            try:
                audio_part = chat_audio_part_from_input_item(item)
            except UnsupportedResponsesInputItemError:
                continue
            has_non_text = True
            parts.append(audio_part)
            continue
        continue
    if not parts:
        return ""
    if not has_non_text and all(part.get("type") == "text" for part in parts):
        return "\n".join(part["text"] for part in parts if isinstance(part.get("text"), str) and part.get("text"))
    return parts


def iter_input_items(payload: ResponsesRequest) -> list[Any]:
    return iter_request_input_items(payload)
