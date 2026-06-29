from __future__ import annotations

from dataclasses import dataclass

from ..bridge_context import BridgeToolContext, custom_tool_input_from_chat_arguments, parse_tool_arguments_object
from ..tool_arguments import canonicalize_tool_arguments


@dataclass
class ToolCallState:
    output_index: int = 0
    item_id: str = ""
    call_id: str = ""
    name: str = ""
    arguments: str = ""
    added: bool = False
    done: bool = False
    reasoning_content: str = ""


@dataclass(frozen=True)
class ToolKind:
    is_custom: bool
    is_tool_search: bool

    @property
    def response_item_type(self) -> str:
        if self.is_custom:
            return "custom_tool_call"
        if self.is_tool_search:
            return "tool_search_call"
        return "function_call"


def resolve_tool_kind(tool_context: BridgeToolContext, name: str | None) -> ToolKind:
    return ToolKind(
        is_custom=tool_context.is_custom_tool(name),
        is_tool_search=tool_context.is_tool_search(name),
    )


def ensure_tool_identity(state: ToolCallState, index: int, kind: ToolKind) -> None:
    if not state.call_id:
        state.call_id = f"call_{index}"
    if not state.name:
        state.name = "unknown_tool"
    state.item_id = f"ctc_{state.call_id}" if kind.is_custom else f"fc_{state.call_id}"


def build_in_progress_item(state: ToolCallState, kind: ToolKind) -> dict:
    item = {
        "id": None if kind.is_tool_search else state.item_id,
        "type": kind.response_item_type,
        "status": "in_progress",
        "call_id": state.call_id,
        "name": None if kind.is_tool_search else state.name,
        "execution": "client" if kind.is_tool_search else None,
        "input": "" if kind.is_custom else None,
        "arguments": {} if kind.is_tool_search else ("" if not kind.is_custom else None),
    }
    item = {key: value for key, value in item.items() if value is not None}
    if state.reasoning_content:
        item["reasoning_content"] = state.reasoning_content
    return item


def build_completed_item(state: ToolCallState, kind: ToolKind) -> tuple[dict, str, str | None]:
    arguments = canonicalize_tool_arguments(state.arguments)
    if kind.is_custom:
        input_text = custom_tool_input_from_chat_arguments(arguments)
        item = {
            "id": state.item_id,
            "type": "custom_tool_call",
            "status": "completed",
            "call_id": state.call_id,
            "name": state.name,
            "input": input_text,
        }
        if state.reasoning_content:
            item["reasoning_content"] = state.reasoning_content
        return item, arguments, input_text
    if kind.is_tool_search:
        item = {
            "type": "tool_search_call",
            "status": "completed",
            "call_id": state.call_id,
            "execution": "client",
            "arguments": parse_tool_arguments_object(arguments),
        }
        if state.reasoning_content:
            item["reasoning_content"] = state.reasoning_content
        return item, arguments, None
    item = {
        "id": state.item_id,
        "type": "function_call",
        "status": "completed",
        "call_id": state.call_id,
        "name": state.name,
        "arguments": arguments,
    }
    if state.reasoning_content:
        item["reasoning_content"] = state.reasoning_content
    return item, arguments, None
