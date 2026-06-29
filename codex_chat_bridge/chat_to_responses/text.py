"""Chat→Responses text and reasoning extraction.

Handles reasoning text extraction (explicit fields + inline think fallback),
content string stripping, and output_text assembly.
"""
from __future__ import annotations

from typing import Any

from ..protocol.types import ChatMessageInput, ContentPart

from .inline_think import split_inline_think


def extract_reasoning_text(message: ChatMessageInput) -> str:
    """Extract reasoning text from a Chat Completions message.

    Checks, in order:
    1. Explicit ``reasoning_content`` / ``reasoning`` fields (standard path)
    2. Inline ``<think>`` blocks embedded in ``content`` (fallback for kimi/GLM/DeepSeek V3)
    """
    # 1. Explicit reasoning field
    for key in ("reasoning_content", "reasoning"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    # 2. Inline think tags in content string
    raw_content = message.get("content")
    if isinstance(raw_content, str) and raw_content.strip():
        result = split_inline_think(raw_content)
        if result.reasoning:
            return result.reasoning

    return ""


def _strip_inline_think_from_content(raw_content: Any) -> Any:
    """If content is a string with inline think, return only the answer part.

    Non-string content is returned unchanged.
    """
    if not isinstance(raw_content, str) or not raw_content.strip():
        return raw_content
    result = split_inline_think(raw_content)
    if result.reasoning:
        return result.answer or ""
    return raw_content


def output_text_from_parts(parts: list[ContentPart]) -> str:
    texts = [part["text"] for part in parts if part.get("type") == "output_text" and isinstance(part.get("text"), str)]
    return "\n".join(texts)
