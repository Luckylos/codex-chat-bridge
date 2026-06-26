from __future__ import annotations

from ..bridge_context import BridgeToolContext
from .envelope import ResponseEnvelopeState
from .tool_events import (
    custom_input_delta,
    custom_input_done,
    function_arguments_delta,
    function_arguments_done,
    output_item_added,
    output_item_done,
)
from .tool_items import (
    ToolCallState,
    build_completed_item,
    build_in_progress_item,
    ensure_tool_identity,
    resolve_tool_kind,
)


class ToolStateStore:
    def __init__(self, tool_context: BridgeToolContext) -> None:
        self.tool_context = tool_context
        self.tool_calls: dict[int, ToolCallState] = {}
        self.finalized = False

    def _ensure_added(
        self,
        envelope: ResponseEnvelopeState,
        state: ToolCallState,
        index: int,
    ) -> tuple[list[bytes], object]:
        if state.added:
            return [], resolve_tool_kind(self.tool_context, state.name)
        state.added = True
        kind = resolve_tool_kind(self.tool_context, state.name)
        ensure_tool_identity(state, index, kind)
        state.output_index = envelope.allocate_output_index()
        item = build_in_progress_item(state, kind)
        return [output_item_added(state.output_index, item)], kind

    def push_delta(self, envelope: ResponseEnvelopeState, tool_call: dict, reasoning: str | None) -> list[bytes]:
        if self.finalized:
            return []
        index = int(tool_call.get("index", 0))
        state = self.tool_calls.setdefault(index, ToolCallState())
        if tool_call.get("id"):
            state.call_id = str(tool_call["id"])
        function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
        if function.get("name"):
            state.name = str(function["name"])
        args_delta = function.get("arguments")
        if isinstance(args_delta, str) and args_delta:
            state.arguments += args_delta
        if reasoning and not state.reasoning_content:
            state.reasoning_content = reasoning

        added_now = not state.added and (state.call_id or state.name)
        events, kind = self._ensure_added(envelope, state, index)
        if added_now and state.arguments and not kind.is_custom:
            events.append(function_arguments_delta(state.item_id, state.output_index, state.arguments))
        elif isinstance(args_delta, str) and args_delta and state.added and not kind.is_custom:
            events.append(function_arguments_delta(state.item_id, state.output_index, args_delta))
        return events

    def finalize(self, envelope: ResponseEnvelopeState) -> list[bytes]:
        if self.finalized:
            return []
        self.finalized = True
        events: list[bytes] = []
        for index, state in sorted(self.tool_calls.items(), key=lambda pair: pair[0]):
            if state.done:
                continue
            if not state.added and (state.call_id or state.name):
                added_events, _ = self._ensure_added(envelope, state, index)
                events.extend(added_events)
            state.done = True
            kind = resolve_tool_kind(self.tool_context, state.name)
            item, arguments, input_text = build_completed_item(state, kind)
            if kind.is_custom:
                if input_text:
                    events.append(custom_input_delta(state.item_id, state.output_index, input_text))
                events.append(custom_input_done(state.item_id, state.output_index, input_text or ""))
            else:
                events.append(function_arguments_done(state.item_id, state.output_index, arguments))
            events.append(output_item_done(state.output_index, item))
            envelope.append_completed_item(state.output_index, item)
        return events
