from __future__ import annotations

from typing import Any

from ..bridge_context import BridgeToolContext, custom_tool_input_from_chat_arguments, parse_tool_arguments_object
from ..response_semantics import canonicalize_tool_arguments


def tool_call_to_response_item(
    call_id: str,
    name: str,
    arguments: object,
    reasoning: str,
    tool_context: BridgeToolContext,
) -> dict[str, Any]:
    canonical_arguments = canonicalize_tool_arguments(arguments)
    spec = tool_context.lookup_chat_name(name)
    if tool_context.is_tool_search(name):
        item: dict[str, Any] = {
            "type": "tool_search_call",
            "status": "completed",
            "call_id": call_id,
            "execution": "client",
            "arguments": parse_tool_arguments_object(canonical_arguments),
        }
    elif tool_context.is_custom_tool(name):
        item = {
            "id": f"ctc_{call_id}",
            "type": "custom_tool_call",
            "status": "completed",
            "call_id": call_id,
            "name": spec.name if spec else name,
            "input": custom_tool_input_from_chat_arguments(canonical_arguments),
        }
    else:
        item = {
            "id": f"fc_{call_id}",
            "type": "function_call",
            "status": "completed",
            "call_id": call_id,
            "name": spec.name if spec else name,
            "arguments": canonical_arguments,
        }
        if spec and spec.namespace:
            item["namespace"] = spec.namespace
    if reasoning:
        item["reasoning_content"] = reasoning
    return item


def chat_tool_calls_to_response_items(
    message: dict[str, Any],
    reasoning: str,
    tool_context: BridgeToolContext,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    tool_calls = message.get("tool_calls") or []
    if isinstance(tool_calls, list):
        for index, tool_call in enumerate(tool_calls):
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
            call_id = str(tool_call.get("id") or f"call_{index}")
            name = str(function.get("name") or "unknown_tool")
            output.append(tool_call_to_response_item(call_id, name, function.get("arguments"), reasoning, tool_context))
    legacy = message.get("function_call")
    if isinstance(legacy, dict):
        call_id = str(legacy.get("id") or "call_0")
        name = str(legacy.get("name") or "unknown_tool")
        output.append(tool_call_to_response_item(call_id, name, legacy.get("arguments"), reasoning, tool_context))
    return output
