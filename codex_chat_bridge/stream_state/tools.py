from __future__ import annotations

import logging

from ..bridge_context import BridgeToolContext, resolve_nested_namespace_arguments
from ..bridge_context.models import ToolSpec
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
    CompletedToolEmission,
    ToolCallState,
    ToolKind,
    build_completed_item,
    build_in_progress_item,
    ensure_tool_identity,
    resolve_tool_kind,
)

_logger = logging.getLogger("codex-chat-bridge")


def _nested_namespace_spec(tool_context: BridgeToolContext, name: str | None) -> ToolSpec | None:
    """Return the ToolSpec when *name* refers to a nested namespace tool."""
    if not name:
        return None
    spec = tool_context.lookup_chat_name(name)
    if spec is not None and spec.is_nested_namespace:
        return spec
    return None


class ToolStateStore:
    def __init__(self, tool_context: BridgeToolContext) -> None:
        self.tool_context = tool_context
        self.tool_calls: dict[int, ToolCallState] = {}
        self.finalized = False

    def _ensure_output_index(self, envelope: ResponseEnvelopeState, state: ToolCallState) -> int:
        if state.output_index is None:
            state.output_index = envelope.allocate_output_index()
        return state.output_index

    def _ensure_added(
        self,
        envelope: ResponseEnvelopeState,
        state: ToolCallState,
        index: int,
    ) -> tuple[list[bytes], ToolKind]:
        if state.added:
            return [], resolve_tool_kind(self.tool_context, state.name)
        state.added = True
        kind = resolve_tool_kind(self.tool_context, state.name)
        ensure_tool_identity(state, index, kind)
        output_index = self._ensure_output_index(envelope, state)
        item = build_in_progress_item(state, kind, self.tool_context)
        return [output_item_added(output_index, item)], kind

    def _apply_tool_call_delta(self, state: ToolCallState, tool_call: dict, reasoning: str | None) -> str | None:
        if tool_call.get("id"):
            state.call_id = str(tool_call["id"])
        raw_function = tool_call.get("function")
        function = raw_function if isinstance(raw_function, dict) else {}
        name = function.get("name")
        if name:
            state.name = str(name)
        args_delta = function.get("arguments")
        if isinstance(args_delta, str) and args_delta:
            state.arguments += args_delta
        if reasoning and not state.reasoning_content:
            state.reasoning_content = reasoning
        return args_delta if isinstance(args_delta, str) and args_delta else None

    def _maybe_start_nested_buffer(self, envelope: ResponseEnvelopeState, state: ToolCallState) -> None:
        if state.added or state.nested_buffered or state.nested_resolved:
            return
        spec = _nested_namespace_spec(self.tool_context, state.name)
        if spec is None:
            return
        _logger.debug(
            "Buffering nested namespace tool call: name=%s, actions=%s",
            state.name,
            spec.actions,
        )
        self._ensure_output_index(envelope, state)
        state.nested_buffered = True

    def _try_resolve_nested_buffer(self, state: ToolCallState) -> bool:
        spec = _nested_namespace_spec(self.tool_context, state.name)
        if spec is None:
            return False
        resolution = resolve_nested_namespace_arguments(spec, state.arguments)
        if resolution.action_name is None:
            return False
        _logger.info(
            "Nested namespace tool call resolved: namespace=%s → action=%s",
            state.name,
            resolution.action_name,
        )
        state.namespace = spec.namespace
        state.name = resolution.action_name
        state.arguments = resolution.normalized_arguments
        state.nested_buffered = False
        state.nested_resolved = True
        return True

    def _emit_buffered_nested_events(
        self,
        envelope: ResponseEnvelopeState,
        state: ToolCallState,
        index: int,
    ) -> list[bytes]:
        if not self._try_resolve_nested_buffer(state):
            return []
        events, kind = self._ensure_added(envelope, state, index)
        if events and state.arguments and not kind.is_custom:
            events.append(function_arguments_delta(state.item_id, state.output_index, state.arguments))
        return events

    def push_delta(self, envelope: ResponseEnvelopeState, tool_call: dict, reasoning: str | None) -> list[bytes]:
        if self.finalized:
            return []

        index = int(tool_call.get("index", 0))
        state = self.tool_calls.setdefault(index, ToolCallState())
        args_delta = self._apply_tool_call_delta(state, tool_call, reasoning)

        if not state.added and (state.call_id or state.name):
            self._maybe_start_nested_buffer(envelope, state)
        if state.nested_buffered:
            return self._emit_buffered_nested_events(envelope, state, index)

        added_now = not state.added and (state.call_id or state.name)
        events, kind = self._ensure_added(envelope, state, index)
        if added_now and state.arguments and not kind.is_custom:
            events.append(function_arguments_delta(state.item_id, state.output_index, state.arguments))
            return events
        if not kind.is_custom and args_delta is not None:
            events.append(function_arguments_delta(state.item_id, state.output_index, args_delta))
        return events

    def _flush_buffered_nested_state(self, state: ToolCallState) -> None:
        if not state.nested_buffered or state.added:
            return
        if self._try_resolve_nested_buffer(state):
            return
        _logger.warning(
            "Nested namespace tool call could not resolve action at finalize: name=%s, emitting as-is",
            state.name,
        )
        state.nested_buffered = False

    def _finalize_state(
        self,
        envelope: ResponseEnvelopeState,
        state: ToolCallState,
        index: int,
    ) -> list[bytes]:
        self._flush_buffered_nested_state(state)
        events: list[bytes] = []
        if not state.added and (state.call_id or state.name):
            added_events, _ = self._ensure_added(envelope, state, index)
            events.extend(added_events)

        state.done = True
        kind = resolve_tool_kind(self.tool_context, state.name)
        emission = build_completed_item(state, kind, self.tool_context)
        if kind.is_custom:
            input_text = emission.input_text or ""
            if input_text:
                events.append(custom_input_delta(state.item_id, state.output_index, input_text))
            events.append(custom_input_done(state.item_id, state.output_index, input_text))
        else:
            events.append(function_arguments_done(state.item_id, state.output_index, emission.arguments))
        events.append(output_item_done(state.output_index, emission.item))
        envelope.append_completed_item(state.output_index, emission.item)
        return events

    def finalize(self, envelope: ResponseEnvelopeState) -> list[bytes]:
        if self.finalized:
            return []
        self.finalized = True
        events: list[bytes] = []
        for index, state in sorted(self.tool_calls.items(), key=lambda pair: pair[0]):
            if state.done:
                continue
            events.extend(self._finalize_state(envelope, state, index))
        return events
