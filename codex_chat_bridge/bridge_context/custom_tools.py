from __future__ import annotations

import json
from typing import Any

from .constants import CUSTOM_TOOL_INPUT_FIELD
from .naming import canonical_json_string


def custom_tool_input_to_chat_arguments(value: Any) -> str:
    return canonical_json_string({CUSTOM_TOOL_INPUT_FIELD: value})


def custom_tool_input_from_chat_arguments(arguments: str) -> str:
    if not arguments.strip():
        return ""
    try:
        parsed = json.loads(arguments)
    except Exception:
        return arguments
    if isinstance(parsed, dict):
        value = parsed.get(CUSTOM_TOOL_INPUT_FIELD)
        if isinstance(value, str):
            return value
    return arguments


def parse_tool_arguments_object(arguments: str) -> dict[str, Any]:
    if not arguments.strip():
        return {}
    try:
        parsed = json.loads(arguments)
    except Exception:
        return {"query": arguments}
    if isinstance(parsed, dict):
        return parsed
    return {"query": arguments}
