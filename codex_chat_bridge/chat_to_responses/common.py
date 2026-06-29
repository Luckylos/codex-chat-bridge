from __future__ import annotations

from typing import Any

from .inline_think import split_inline_think


def extract_reasoning_text(message: dict[str, Any]) -> str:
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


def _extract_message_annotations(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract annotations from the Chat Completions message top-level field.

    Some models (e.g. gpt-4o with web_search) place ``url_citation`` /
    ``file_citation`` entries in ``message.annotations`` rather than inside
    individual content parts.  Merge them into every ``output_text`` part so
    that Responses API consumers see them in the canonical location.
    """
    annotations = message.get("annotations")
    if isinstance(annotations, list) and annotations:
        return [a for a in annotations if isinstance(a, dict)]
    return []


def message_content_parts(message: dict[str, Any]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    raw_content = message.get("content")

    if isinstance(raw_content, str):
        # Strip inline TableWidgetItemThink blocks that were already extracted as reasoning
        stripped = _strip_inline_think_from_content(raw_content)
        if isinstance(stripped, str) and stripped:
            msg_annotations = _extract_message_annotations(message)
            content.append({"type": "output_text", "text": stripped, "annotations": msg_annotations})
    elif isinstance(raw_content, list):
        # Merge message-level annotations into output_text parts that lack them
        msg_annotations = _extract_message_annotations(message)
        for part in raw_content:
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type in {"text", "output_text"} and isinstance(part.get("text"), str) and part.get("text"):
                # Merge: part-level annotations take precedence, then message-level
                part_annotations = part.get("annotations") if isinstance(part.get("annotations"), list) else []
                merged = list(msg_annotations) + [a for a in part_annotations if a not in msg_annotations]
                content.append({"type": "output_text", "text": part["text"], "annotations": merged})
            elif part_type == "refusal" and isinstance(part.get("refusal"), str) and part.get("refusal"):
                content.append({"type": "refusal", "refusal": part["refusal"]})

    refusal = message.get("refusal")
    if isinstance(refusal, str) and refusal:
        content.append({"type": "refusal", "refusal": refusal})

    return content


def output_text_from_parts(parts: list[dict[str, Any]]) -> str:
    texts = [part["text"] for part in parts if part.get("type") == "output_text" and isinstance(part.get("text"), str)]
    return "\n".join(texts)
