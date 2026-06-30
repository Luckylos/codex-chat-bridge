"""SSE frame parsing and serialization — pure functions, no external dependencies.

Moved from sse_utils.py into the protocol subpackage for better
cohesion with other protocol-layer modules.
"""
from __future__ import annotations

import json
from typing import Any


def extract_block(buffer: str) -> tuple[str, str] | None:
    """Extract the first complete SSE frame block from buffer.

    Returns (block, remaining_buffer) or None if no complete frame is found.
    Frame delimiter is two consecutive newlines (\\n\\n).
    """
    marker = "\n\n"
    idx = buffer.find(marker)
    if idx == -1:
        return None
    return buffer[:idx], buffer[idx + len(marker):]


def parse_sse_block(block: str) -> tuple[str | None, str | None]:
    """Parse an SSE block, extracting event type and data content.

    Returns (event_name, data_string). event_name may be None.
    """
    event_name: str | None = None
    data_parts: list[str] = []
    for line in block.splitlines():
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            # SSE spec: remove "data:" prefix and exactly one leading space
            # (if present).  Do NOT strip all whitespace — intentional
            # multi-space content must be preserved.
            raw = line.split(":", 1)[1]
            data_parts.append(raw[1:] if raw.startswith(" ") else raw)
    data = "\n".join(data_parts) if data_parts else None
    return event_name, data


def parse_sse_json_block(block: str) -> tuple[str | None, dict | None]:
    """Parse an SSE block and attempt to parse its data as JSON.

    Returns (event_name, parsed_json_dict) or (event_name, None).
    """
    event, data = parse_sse_block(block)
    if data:
        try:
            return event, json.loads(data)
        except json.JSONDecodeError:
            return event, None
    return event, None


def serialize_event(event: str | None, data: Any) -> bytes:
    """Serialize a single SSE event to bytes.

    If event is None or empty, no event: line is written.
    data is JSON-serialized.
    """
    parts: list[str] = []
    if event:
        parts.append(f"event: {event}")
    parts.append(f"data: {json.dumps(data, ensure_ascii=False)}")
    parts.append("")
    return ("\n".join(parts) + "\n").encode("utf-8")


def sse_event(event: str, data: Any) -> bytes:
    """Convenience function: generate an SSE event with an event name."""
    return serialize_event(event, data)


def sse_done() -> bytes:
    """Generate the SSE [DONE] termination marker."""
    return b"data: [DONE]\n\n"


def iter_sse_bytes_as_list(
    chunks: list[str],
) -> list[tuple[str | None, dict | None]]:
    """Parse an SSE chunk list into a list of (event, parsed_data) tuples.

    For testing and debugging. Production code should use
    stream_chat_to_responses serialization.
    """
    result: list[tuple[str | None, dict | None]] = []
    buffer = ""
    for chunk in chunks:
        buffer += chunk
        while True:
            extracted = extract_block(buffer)
            if extracted is None:
                break
            block, buffer = extracted
            if not block.strip():
                continue
            result.append(parse_sse_json_block(block))
    return result
