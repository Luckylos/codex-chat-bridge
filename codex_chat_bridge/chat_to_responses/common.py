from __future__ import annotations

from typing import Any


def extract_reasoning_text(message: dict[str, Any]) -> str:
    for key in ("reasoning_content", "reasoning"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def message_content_parts(message: dict[str, Any]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    raw_content = message.get("content")

    if isinstance(raw_content, str):
        if raw_content:
            content.append({"type": "output_text", "text": raw_content, "annotations": []})
    elif isinstance(raw_content, list):
        for part in raw_content:
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type in {"text", "output_text"} and isinstance(part.get("text"), str) and part.get("text"):
                content.append({"type": "output_text", "text": part["text"], "annotations": []})
            elif part_type == "refusal" and isinstance(part.get("refusal"), str) and part.get("refusal"):
                content.append({"type": "refusal", "refusal": part["refusal"]})

    refusal = message.get("refusal")
    if isinstance(refusal, str) and refusal:
        content.append({"type": "refusal", "refusal": refusal})

    return content


def output_text_from_parts(parts: list[dict[str, Any]]) -> str:
    texts = [part["text"] for part in parts if part.get("type") == "output_text" and isinstance(part.get("text"), str)]
    return "\n".join(texts)
