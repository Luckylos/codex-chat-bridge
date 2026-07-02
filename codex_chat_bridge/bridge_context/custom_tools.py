from __future__ import annotations

import json
from typing import Any

from .constants import CUSTOM_TOOL_INPUT_FIELD
from .naming import canonical_json_string


def custom_tool_input_to_chat_arguments(value: Any) -> str:
    return canonical_json_string({CUSTOM_TOOL_INPUT_FIELD: value})


def _partial_json_string_prefix(text: str) -> str:
    result: list[str] = []
    i = 0
    length = len(text)
    while i < length:
        ch = text[i]
        if ch == '"':
            break
        if ch != "\\":
            result.append(ch)
            i += 1
            continue
        if i + 1 >= length:
            break
        esc = text[i + 1]
        escapes = {
            '"': '"',
            '\\': '\\',
            '/': '/',
            'b': '\b',
            'f': '\f',
            'n': '\n',
            'r': '\r',
            't': '\t',
        }
        if esc == 'u':
            if i + 6 > length:
                break
            hex_digits = text[i + 2 : i + 6]
            try:
                result.append(chr(int(hex_digits, 16)))
            except ValueError:
                break
            i += 6
            continue
        mapped = escapes.get(esc)
        if mapped is None:
            break
        result.append(mapped)
        i += 2
    return ''.join(result)


def partial_custom_tool_input_from_chat_arguments(arguments: str) -> str | None:
    if not arguments.strip():
        return None

    key_pos = arguments.find(f'"{CUSTOM_TOOL_INPUT_FIELD}"')
    if key_pos < 0:
        return None
    colon_pos = arguments.find(':', key_pos)
    if colon_pos < 0:
        return None

    value_start = colon_pos + 1
    while value_start < len(arguments) and arguments[value_start] in ' \t\r\n':
        value_start += 1
    if value_start >= len(arguments) or arguments[value_start] != '"':
        return None

    return _partial_json_string_prefix(arguments[value_start + 1 :])


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
