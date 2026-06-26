from __future__ import annotations

import hashlib
import json
from typing import Any

from .constants import CHAT_TOOL_NAME_MAX_LEN


def canonical_json_string(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def short_sha256_hex(bytes_value: bytes) -> str:
    return hashlib.sha256(bytes_value).hexdigest()[:16]


def flatten_namespace_tool_name(namespace: str, name: str) -> str:
    full_name = f"{namespace}__{name}"
    if len(full_name) <= CHAT_TOOL_NAME_MAX_LEN:
        return full_name
    suffix = f"__{short_sha256_hex(full_name.encode())}"
    prefix_len = CHAT_TOOL_NAME_MAX_LEN - len(suffix)
    prefix = full_name[:prefix_len]
    return f"{prefix}{suffix}"


def tool_name_from_value(tool: Any) -> str | None:
    if isinstance(tool, str):
        candidate = tool.strip()
        return candidate or None
    if not isinstance(tool, dict):
        return None
    name = tool.get("name")
    if isinstance(name, str) and name.strip():
        return name
    function = tool.get("function")
    if isinstance(function, dict):
        nested = function.get("name")
        if isinstance(nested, str) and nested.strip():
            return nested
    return None
