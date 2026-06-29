"""Chat→Responses annotation extraction and merging.

Some models (e.g. gpt-4o with web_search) place ``url_citation`` /
``file_citation`` entries in ``message.annotations`` at the top level,
not inside individual content parts. This module extracts message-level
annotations and merges them with part-level annotations into the
canonical Responses API location (inside output_text parts).
"""
from __future__ import annotations

from typing import Any

from .text import _strip_inline_think_from_content


def extract_message_annotations(message: dict[str, Any]) -> list[dict[str, Any]]:
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
    """Build Responses output_text/refusal content parts from a Chat Completions message.

    Merges message-level annotations into each output_text part, deduplicating
    against any part-level annotations already present.
    """
    content: list[dict[str, Any]] = []
    raw_content = message.get("content")

    if isinstance(raw_content, str):
        # Strip inline <think> blocks that were already extracted as reasoning
        stripped = _strip_inline_think_from_content(raw_content)
        if isinstance(stripped, str) and stripped:
            msg_annotations: list[dict[str, Any]] = extract_message_annotations(message)
            content.append({"type": "output_text", "text": stripped, "annotations": msg_annotations})
    elif isinstance(raw_content, list):
        # Merge message-level annotations into output_text parts that lack them
        msg_annotations: list[dict[str, Any]] = extract_message_annotations(message)
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
