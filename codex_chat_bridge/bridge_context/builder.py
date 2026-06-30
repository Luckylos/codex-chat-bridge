from __future__ import annotations

from typing import Any

from ..models import ResponsesRequest
from .context import BridgeToolContext
from .models import ToolSpec


def iter_request_input_items(payload: ResponsesRequest) -> list[Any]:
    if payload.input is None:
        return []
    if isinstance(payload.input, str):
        return [{"type": "input_text", "text": payload.input}]
    if isinstance(payload.input, list):
        return payload.input
    return [payload.input]


def collect_tool_search_output_tools(value: Any, context: BridgeToolContext) -> None:
    if isinstance(value, list):
        for item in value:
            collect_tool_search_output_tools(item, context)
        return
    if not isinstance(value, dict):
        return
    if value.get("type") == "tool_search_output":
        tools = value.get("tools")
        if isinstance(tools, list):
            for tool in tools:
                context.add_response_tool(tool)
        return  # Don't recurse into tool_search_output values


def build_tool_context_from_request(payload: ResponsesRequest) -> BridgeToolContext:
    context = BridgeToolContext()

    for tool in payload.tools or []:
        context.add_response_tool(tool)

    for item in iter_request_input_items(payload):
        if isinstance(item, dict) and item.get("type") == "custom_tool_call":
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                context.custom_tool_names.add(name)
                context.chat_name_to_spec.setdefault(name, ToolSpec(kind="custom", name=name))
        collect_tool_search_output_tools(item, context)

    return context
