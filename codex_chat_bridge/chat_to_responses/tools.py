from __future__ import annotations

import json

from ..protocol.types import ChatMessageInput, ResponsesToolCallItem

from ..bridge_context import BridgeToolContext, custom_tool_input_from_chat_arguments, parse_tool_arguments_object
from ..bridge_context.models import ToolSpec
from ..tool_arguments import canonicalize_tool_arguments


def tool_call_to_response_item(
    call_id: str,
    name: str,
    arguments: object,
    reasoning: str,
    tool_context: BridgeToolContext,
) -> ResponsesToolCallItem:
    canonical_arguments = canonicalize_tool_arguments(arguments)
    spec = tool_context.lookup_chat_name(name)
    if tool_context.is_tool_search(name):
        item: ResponsesToolCallItem = {
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
    elif spec and spec.kind == "namespace" and spec.namespace_strategy in ("nested_oneof", "nested_anyof"):
        # Nested namespace call — extract action from arguments JSON
        return _nested_namespace_call_to_response_item(
            call_id, spec, canonical_arguments, reasoning,
        )
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


def _nested_namespace_call_to_response_item(
    call_id: str,
    spec: ToolSpec,
    canonical_arguments: str,
    reasoning: str,
) -> ResponsesToolCallItem:
    """Convert a namespace-level Chat tool call back to a Responses function_call.

    For nested namespace calls, the upstream model returns the namespace name
    with an ``action`` key inside the arguments JSON.  We extract the action
    and reconstruct a standard Responses ``function_call`` item with the
    concrete action name and stripped-down arguments.
    """
    action_name: str | None = None
    clean_arguments = canonical_arguments

    try:
        args_obj = json.loads(canonical_arguments)
        if isinstance(args_obj, dict):
            action_val = args_obj.pop("action", None)
            if isinstance(action_val, str) and action_val:
                action_name = action_val
            if spec.namespace_strategy == "nested_anyof":
                params_val = args_obj.pop("params", None)
                if isinstance(params_val, dict):
                    args_obj.update(params_val)
            clean_arguments = json.dumps(args_obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (json.JSONDecodeError, ValueError):
        pass

    # Use the extracted action name if valid; fall back to namespace name.
    response_name = action_name if action_name and spec.actions and action_name in spec.actions else spec.name

    item: ResponsesToolCallItem = {
        "id": f"fc_{call_id}",
        "type": "function_call",
        "status": "completed",
        "call_id": call_id,
        "name": response_name,
        "arguments": clean_arguments,
    }
    if spec.namespace:
        item["namespace"] = spec.namespace
    if reasoning:
        item["reasoning_content"] = reasoning
    return item


def chat_tool_calls_to_response_items(
    message: ChatMessageInput,
    reasoning: str,
    tool_context: BridgeToolContext,
) -> list[ResponsesToolCallItem]:
    output: list[ResponsesToolCallItem] = []
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
