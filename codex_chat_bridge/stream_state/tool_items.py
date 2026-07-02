from __future__ import annotations

from dataclasses import dataclass

from ..bridge_context import (
    BridgeToolContext,
    custom_tool_input_from_chat_arguments,
    parse_tool_arguments_object,
)
from ..tool_arguments import canonicalize_tool_arguments


@dataclass
class ToolCallState:
    output_index: int | None = None
    item_id: str = ""
    call_id: str = ""
    name: str = ""
    namespace: str | None = None
    arguments: str = ""
    added: bool = False
    done: bool = False
    reasoning_content: str = ""
    emitted_custom_input: str = ""
    # Nested namespace buffering: when upstream returns a namespace-level
    # name instead of a concrete action, we delay emitting output_item.added
    # until we can extract the action from the arguments JSON.
    nested_buffered: bool = False
    nested_resolved: bool = False


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


@dataclass(frozen=True)
class CompletedToolEmission:
    item: dict
    arguments: str
    input_text: str | None


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


def _with_reasoning_content(state: ToolCallState, item: dict) -> dict:
    if state.reasoning_content:
        item["reasoning_content"] = state.reasoning_content
    return item


def _response_name_and_namespace(
    state: ToolCallState,
    kind: ToolKind,
    tool_context: BridgeToolContext,
) -> tuple[str | None, str | None]:
    if kind.is_tool_search:
        return None, None
    if kind.is_custom:
        return state.name, None
    if state.namespace:
        return state.name, state.namespace
    namespace, restored_name = tool_context.restore_namespace_and_name(state.name)
    if namespace is not None:
        return restored_name, namespace
    return state.name, None


def build_in_progress_item(state: ToolCallState, kind: ToolKind, tool_context: BridgeToolContext) -> dict:
    response_name, namespace = _response_name_and_namespace(state, kind, tool_context)
    item = {
        "id": state.item_id,
        "type": kind.response_item_type,
        "status": "in_progress",
        "call_id": state.call_id,
        "name": response_name,
        "namespace": namespace,
        "execution": "client" if kind.is_tool_search else None,
        "input": "" if kind.is_custom else None,
        "arguments": {} if kind.is_tool_search else ("" if not kind.is_custom else None),
    }
    item = {key: value for key, value in item.items() if value is not None}
    return _with_reasoning_content(state, item)


def build_completed_item(
    state: ToolCallState,
    kind: ToolKind,
    tool_context: BridgeToolContext,
) -> CompletedToolEmission:
    arguments = canonicalize_tool_arguments(state.arguments)
    response_name, namespace = _response_name_and_namespace(state, kind, tool_context)
    if kind.is_custom:
        input_text = custom_tool_input_from_chat_arguments(arguments)
        item = _with_reasoning_content(
            state,
            {
                "id": state.item_id,
                "type": "custom_tool_call",
                "status": "completed",
                "call_id": state.call_id,
                "name": response_name,
                "input": input_text,
            },
        )
        return CompletedToolEmission(item=item, arguments=arguments, input_text=input_text)
    if kind.is_tool_search:
        item = _with_reasoning_content(
            state,
            {
                "id": state.item_id,
                "type": "tool_search_call",
                "status": "completed",
                "call_id": state.call_id,
                "execution": "client",
                "arguments": parse_tool_arguments_object(arguments),
            },
        )
        return CompletedToolEmission(item=item, arguments=arguments, input_text=None)
    item = _with_reasoning_content(
        state,
        {
            "id": state.item_id,
            "type": "function_call",
            "status": "completed",
            "call_id": state.call_id,
            "name": response_name,
            "namespace": namespace,
            "arguments": arguments,
        },
    )
    item = {key: value for key, value in item.items() if value is not None}
    return CompletedToolEmission(item=item, arguments=arguments, input_text=None)
