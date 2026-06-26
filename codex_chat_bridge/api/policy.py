from __future__ import annotations

from fastapi.responses import JSONResponse

from ..models import ChatCompletionsRequest, ChatMessage
from .errors import invalid_request_error


def message_has_semantic_content(message: ChatMessage) -> bool:
    if message.tool_calls:
        return True

    content = message.content
    if isinstance(content, str):
        return bool(content.strip())
    if not isinstance(content, list):
        return False

    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type == "text" and isinstance(part.get("text"), str) and part["text"].strip():
            return True
        if part_type == "image_url":
            image_value = part.get("image_url")
            if isinstance(image_value, str) and image_value.strip():
                return True
            if isinstance(image_value, dict) and isinstance(image_value.get("url"), str) and image_value["url"].strip():
                return True
    return False


def validate_effective_messages(chat_request: ChatCompletionsRequest) -> JSONResponse | None:
    if not chat_request.messages:
        return invalid_request_error(
            "No supported Responses input items remained after bridge normalization.",
            "empty_effective_input",
        )
    if not any(message_has_semantic_content(message) for message in chat_request.messages):
        return invalid_request_error(
            "Responses input normalized to only blank or semantically empty messages.",
            "blank_effective_input",
        )
    return None
