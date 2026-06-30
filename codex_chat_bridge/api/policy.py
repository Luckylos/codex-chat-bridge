"""Effective-input UX guard — validates that the normalized request
still carries meaningful content before forwarding to upstream."""
from __future__ import annotations

from typing import Any

from ..errors import InvalidRequestError
from ..models import ChatCompletionsRequest, ChatMessage


def _has_nonblank_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _image_part_has_semantic_content(part: dict[str, Any]) -> bool:
    image_value = part.get("image_url")
    if _has_nonblank_string(image_value):
        return True
    return isinstance(image_value, dict) and _has_nonblank_string(image_value.get("url"))


def _audio_part_has_semantic_content(part: dict[str, Any]) -> bool:
    audio_value = part.get("input_audio")
    if isinstance(audio_value, dict):
        return _has_nonblank_string(audio_value.get("url")) or _has_nonblank_string(audio_value.get("data"))

    # Compatibility with any legacy normalized shape that still carries audio_url directly.
    return _has_nonblank_string(part.get("audio_url"))


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
        if part_type == "text" and _has_nonblank_string(part.get("text")):
            return True
        if part_type == "image_url" and _image_part_has_semantic_content(part):
            return True
        if part_type == "input_audio" and _audio_part_has_semantic_content(part):
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
