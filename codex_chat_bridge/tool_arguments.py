"""Tool argument canonicalization.

Shared between the Chat→Responses and Responses→Chat conversion paths
to ensure consistent JSON canonicalization of tool call arguments.
"""
from __future__ import annotations

import json
from typing import Any


def canonicalize_tool_arguments(arguments: object) -> str:
    """Canonicalize tool arguments into a deterministic JSON string.

    - None → "{}"
    - String: parse and re-serialize with sorted keys
    - Other: JSON-serialize with sorted keys
    - Unparseable strings: returned as-is
    """
    if arguments is None:
        return "{}"
    if isinstance(arguments, str):
        raw = arguments.strip()
        if not raw:
            return "{}"
        try:
            parsed = json.loads(raw)
        except Exception:
            return arguments
        return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return json.dumps(arguments, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
