"""Effective-input UX guard — validates that the normalized request
still carries meaningful content before forwarding to upstream."""
from __future__ import annotations

from ..errors import InvalidRequestError
from ..models import ChatCompletionsRequest, ChatMessage


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


def validate_effective_messages(chat_request: ChatCompletionsRequest) -> None:
    """Raise InvalidRequestError if the request has no semantically meaningful content.

    This prevents obviously-empty requests from reaching upstream.
    """
    if not chat_request.messages:
        raise InvalidRequestError(
            "No supported Responses input items remained after bridge normalization.",
            code="empty_effective_input",
        )
    if not any(message_has_semantic_content(message) for message in chat_request.messages):
        raise InvalidRequestError(
            "Responses input normalized to only blank or semantically empty messages.",
            code="blank_effective_input",
        )
