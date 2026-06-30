from __future__ import annotations

from typing import Any

from ..bridge_context import iter_request_input_items
from ..models import ResponsesRequest
from .content import flatten_text_content
from .errors import UnsupportedResponsesInputItemError
from .media import chat_audio_part_from_input_item, chat_image_part_from_input_item


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
                parts.append({"type": "text", "text": f"[refusal]: {item['refusal']}"})
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
