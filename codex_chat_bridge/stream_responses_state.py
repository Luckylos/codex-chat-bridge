from __future__ import annotations

from typing import Any

from .bridge_context import BridgeToolContext
from .inline_think_sm import InlineThinkStateMachine
from .models import ChatMessage
from .protocol.sse import sse_event
from .responses_to_chat.content_mapping import chat_message_content_from_response_content
from .stream_state import MessageState, ReasoningState, ResponseEnvelopeState, ToolStateStore


class ResponsesStreamState:
    def __init__(self, tool_context: BridgeToolContext | None = None, response_id: str | None = None) -> None:
        resolved_context = tool_context or BridgeToolContext()
        self.envelope = ResponseEnvelopeState(response_id=response_id)
        self.reasoning = ReasoningState()
        self.message = MessageState()
        self.tools = ToolStateStore(resolved_context)
        self.inline_think = InlineThinkStateMachine()

    def apply_chunk_metadata(self, payload: dict) -> None:
        self.envelope.apply_metadata(payload)

    def ensure_started(self) -> list[bytes]:
        return self.envelope.ensure_started()

    def push_reasoning_delta(self, delta: str) -> list[bytes]:
        return self.reasoning.push_delta(self.envelope, delta)

    def active_reasoning_text_for_tools(self) -> str:
        return self.reasoning.active_text_for_tools()

    def finalize_reasoning_if_open(self) -> list[bytes]:
        return self.reasoning.finalize(self.envelope)

    def push_tool_call_delta(self, tool_call: dict, reasoning: str | None) -> list[bytes]:
        return self.tools.push_delta(self.envelope, tool_call, reasoning)

    def push_text_delta(self, delta: str) -> list[bytes]:
        return self.message.push_text_delta(self.envelope, delta)

    def push_content_delta(self, delta: str) -> list[bytes]:
        """Route a content delta through the inline-think state machine.

        Delegates to InlineThinkStateMachine for three-phase detection,
        which calls back into this state's push_reasoning_delta /
        push_text_delta / finalize_reasoning_if_open as needed.
        """
        return self.inline_think.push_content_delta(delta, self)

    def push_refusal_part(self, refusal: str) -> list[bytes]:
        return self.message.push_refusal_part(self.envelope, refusal)

    def set_finish_reason(self, finish_reason: str) -> None:
        self.envelope.finish_reason = finish_reason

    def _flush_open_items(self) -> list[bytes]:
        events: list[bytes] = []
        events.extend(self.envelope.ensure_started())
        events.extend(self.inline_think.flush_on_finalize(self))
        events.extend(self.reasoning.finalize(self.envelope))
        events.extend(self.message.finalize(self.envelope))
        events.extend(self.tools.finalize(self.envelope))
        return events

    def _completion_status(self) -> str:
        finish_reason = self.envelope.finish_reason
        if finish_reason in ("length", "content_filter"):
            return "incomplete"
        if finish_reason == "tool_calls":
            return "in_progress"
        return "completed"

    def _incomplete_reason(self) -> str | None:
        if self.envelope.finish_reason == "content_filter":
            return "content_filter"
        if self.envelope.finish_reason == "length":
            return "max_output_tokens"
        return None

    def _response_completed_event(self, status: str, output: list[dict]) -> bytes:
        response = self.envelope.base_response(status, output)
        incomplete_reason = self._incomplete_reason()
        if incomplete_reason is not None:
            response["incomplete_details"] = {"reason": incomplete_reason}
        return sse_event("response.completed", {"type": "response.completed", "response": response})

    def _response_failed_event(self, message: str, error_type: str) -> bytes:
        response = self.envelope.base_response("failed", self.envelope.completed_output_items())
        response["error"] = {"message": message, "type": error_type}
        return sse_event("response.failed", {"type": "response.failed", "response": response})

    def finalize(self) -> list[bytes]:
        if self.envelope.completed:
            return []
        self.envelope.completed = True
        events = self._flush_open_items()
        output = self.envelope.completed_output_items()

        if self.envelope.finish_reason is None:
            if output:
                response = self.envelope.base_response("incomplete", output)
                response["incomplete_details"] = {"reason": "stream_truncated"}
                events.append(sse_event("response.completed", {"type": "response.completed", "response": response}))
            else:
                events.extend(self.fail("Stream truncated before any output was produced", "stream_truncated"))
            return events

        events.append(self._response_completed_event(self._completion_status(), output))
        return events

    def fail(self, message: str, error_type: str = "stream_error") -> list[bytes]:
        self.envelope.completed = True
        events = self._flush_open_items()
        events.append(self._response_failed_event(message, error_type))
        return events

    def _assistant_message_content(self) -> Any:
        content_parts = self.message.content_parts()
        if content_parts:
            return chat_message_content_from_response_content(content_parts)
        if self.message.text:
            return self.message.text
        return None

    def _assistant_tool_calls(self) -> list[dict] | None:
        tool_call_states = {k: v for k, v in self.tools.tool_calls.items() if v.name}
        if not tool_call_states:
            return None

        chat_tool_calls: list[dict] = []
        for index, state in sorted(tool_call_states.items(), key=lambda pair: pair[0]):
            chat_tool_calls.append(
                {
                    "id": state.call_id or f"call_{index}",
                    "type": "function",
                    "function": {
                        "name": state.name,
                        "arguments": state.arguments,
                    },
                }
            )
        return chat_tool_calls

    def build_assistant_message(self) -> ChatMessage | None:
        """Build an assistant ChatMessage for session persistence."""
        tool_calls = self._assistant_tool_calls()
        content = self._assistant_message_content()
        has_visible_content = content is not None

        if not has_visible_content and not tool_calls and not self.reasoning.text:
            return None

        reasoning = self.reasoning.text.strip() or None
        return ChatMessage(
            role="assistant",
            content=content,
            tool_calls=tool_calls,
            reasoning_content=reasoning,
        )
