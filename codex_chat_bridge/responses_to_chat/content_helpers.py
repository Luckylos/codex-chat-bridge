from __future__ import annotations

from typing import Any


def flatten_text_content(content: Any) -> str:
    """Flatten structured content to plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    chunks: list[str] = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                typ = item.get("type")
                if typ in {"input_text", "output_text", "text"} and isinstance(item.get("text"), str):
                    chunks.append(item["text"])
            elif isinstance(item, str):
                chunks.append(item)
    return "\n".join(chunk for chunk in chunks if chunk)


def instruction_text(value: Any) -> str:
    """Extract plain-text instructions from a Responses instructions field."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        chunks: list[str] = []
        for part in value:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                if part["text"]:
                    chunks.append(part["text"])
            elif isinstance(part, str) and part:
                chunks.append(part)
        return "\n\n".join(chunks)
    return str(value) if value is not None else ""


def reasoning_item_text(item: dict[str, Any]) -> str:
    """Extract text from a Responses reasoning item."""
    summary = item.get("summary")
    if isinstance(summary, list):
        chunks: list[str] = []
        for part in summary:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                chunks.append(part["text"])
        return "\n\n".join(chunk for chunk in chunks if chunk)
    if isinstance(item.get("text"), str):
        return item["text"]
    return ""


def normalize_tool_output_content(value: Any) -> str:
    """Normalize a tool output value to a plain-text string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if isinstance(value.get("content"), list):
            flattened = flatten_text_content(value.get("content"))
            if flattened:
                return flattened
        if value.get("type") in {"input_text", "output_text", "text"} and isinstance(value.get("text"), str):
            return value["text"]
    if isinstance(value, list):
        flattened = flatten_text_content(value)
        if flattened:
            return flattened
    from ..bridge_context import canonical_json_string
    return canonical_json_string(value)
