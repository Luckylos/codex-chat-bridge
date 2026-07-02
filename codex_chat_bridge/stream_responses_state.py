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

    def finalize(self) -> list[bytes]:
        if self.envelope.completed:
            return []
        self.envelope.completed = True
        events: list[bytes] = []
        events.extend(self.envelope.ensure_started())

        events.extend(self.inline_think.flush_on_finalize(self))
        events.extend(self.reasoning.finalize(self.envelope))
        events.extend(self.message.finalize(self.envelope))
        events.extend(self.tools.finalize(self.envelope))

        output = self.envelope.completed_output_items()
        if self.envelope.finish_reason is None:
            if output:
                response = self.envelope.base_response("incomplete", output)
                response["incomplete_details"] = {"reason": "stream_truncated"}
                events.append(sse_event("response.completed", {"type": "response.completed", "response": response}))
            else:
                events.extend(self.fail("Stream truncated before any output was produced", "stream_truncated"))
            return events

        status = (
            "incomplete"
            if self.envelope.finish_reason in ("length", "content_filter")
            else ("in_progress" if self.envelope.finish_reason == "tool_calls" else "completed")
        )
        response = self.envelope.base_response(status, output)
        if status == "incomplete":
            reason = "content_filter" if self.envelope.finish_reason == "content_filter" else "max_output_tokens"
            response["incomplete_details"] = {"reason": reason}
        events.append(sse_event("response.completed", {"type": "response.completed", "response": response}))
        return events

    def fail(self, message: str, error_type: str = "stream_error") -> list[bytes]:
        self.envelope.completed = True
        events: list[bytes] = []
        events.extend(self.envelope.ensure_started())
        events.extend(self.inline_think.flush_on_finalize(self))
        events.extend(self.reasoning.finalize(self.envelope))
        events.extend(self.message.finalize(self.envelope))
        events.extend(self.tools.finalize(self.envelope))
        response = self.envelope.base_response("failed", self.envelope.completed_output_items())
        response["error"] = {"message": message, "type": error_type}
        events.append(sse_event("response.failed", {"type": "response.failed", "response": response}))
        return events

    def build_assistant_message(self) -> ChatMessage | None:
        """Build an assistant ChatMessage for session persistence."""
        tool_call_states = {k: v for k, v in self.tools.tool_calls.items() if v.name}
        content_parts = self.message.content_parts()
        has_visible_content = bool(content_parts) or bool(self.message.text)

        if not has_visible_content and not tool_call_states:
            if not self.reasoning.text:
                return None

        if content_parts:
            content = chat_message_content_from_response_content(content_parts)
        elif self.message.text:
            content = self.message.text
        else:
            content = None

        chat_tool_calls = None
        if tool_call_states:
            chat_tool_calls = []
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

        reasoning = self.reasoning.text.strip() or None
        return ChatMessage(
            role="assistant",
            content=content,
            tool_calls=chat_tool_calls,
            reasoning_content=reasoning,
        )
