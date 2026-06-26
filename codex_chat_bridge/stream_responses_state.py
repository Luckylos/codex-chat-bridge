from __future__ import annotations

from .bridge_context import BridgeToolContext
from .stream_state import MessageState, ReasoningState, ResponseEnvelopeState, ToolStateStore, sse_event


class ResponsesStreamState:
    def __init__(self, tool_context: BridgeToolContext | None = None) -> None:
        resolved_context = tool_context or BridgeToolContext()
        self.envelope = ResponseEnvelopeState()
        self.reasoning = ReasoningState()
        self.message = MessageState()
        self.tools = ToolStateStore(resolved_context)

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
                events.append(self.fail("Stream truncated before any output was produced", "stream_truncated"))
            return events
        status = "incomplete" if self.envelope.finish_reason == "length" else "completed"
        response = self.envelope.base_response(status, output)
        if status == "incomplete":
            response["incomplete_details"] = {"reason": "max_output_tokens"}
        events.append(sse_event("response.completed", {"type": "response.completed", "response": response}))
        return events

    def fail(self, message: str, error_type: str = "stream_error") -> bytes:
        self.envelope.completed = True
        response = self.envelope.base_response("failed", self.envelope.completed_output_items())
        response["error"] = {"message": message, "type": error_type}
        return sse_event("response.failed", {"type": "response.failed", "response": response})
