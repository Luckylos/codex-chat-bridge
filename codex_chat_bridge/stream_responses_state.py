from __future__ import annotations

from typing import Any

from .bridge_context import BridgeToolContext
from .sse_utils import sse_event
from .stream_state import MessageState, ReasoningState, ResponseEnvelopeState, ToolStateStore


class ResponsesStreamState:
    # Inline-think detection phases: models like kimi/GLM embed  in content
    _PHASE_DETECTING = "detecting"
    _PHASE_REASONING = "reasoning"
    _PHASE_TEXT = "text"

    def __init__(self, tool_context: BridgeToolContext | None = None, response_id: str | None = None) -> None:
        resolved_context = tool_context or BridgeToolContext()
        self.envelope = ResponseEnvelopeState(response_id=response_id)
        self.reasoning = ReasoningState()
        self.message = MessageState()
        self.tools = ToolStateStore(resolved_context)
        # Inline think detection state
        self._inline_think_phase: str = self._PHASE_DETECTING
        self._inline_think_buffer: str = ""

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
        """Route a content delta through inline-think detection.

        If the delta starts with (or continues) a  block, route
        reasoning content to the reasoning state machine; once the
        think block closes, switch to normal text emission.
        """
        import re as _re
        from .chat_to_responses.inline_think import could_be_partial_think_open

        if self._inline_think_phase == self._PHASE_TEXT:
            # Already past the think block — emit as plain text
            return self.push_text_delta(delta)

        if self._inline_think_phase == self._PHASE_REASONING:
            # Inside a  think block — look for closing tag
            close_re = _re.compile(r"</(?:think|thinking)\s*>", _re.IGNORECASE)
            m = close_re.search(delta)
            if m:
                # Close tag found: emit pre-close as reasoning, switch to text
                events: list[bytes] = []
                pre = delta[:m.start()]
                if pre:
                    events.extend(self.push_reasoning_delta(pre))
                events.extend(self.finalize_reasoning_if_open())
                self._inline_think_phase = self._PHASE_TEXT
                post = delta[m.end():]
                if post:
                    events.extend(self.push_text_delta(post))
                return events
            # No close tag yet — entire delta is reasoning
            return self.push_reasoning_delta(delta)

        # Phase: DETECTING — accumulate to check for  prefix
        self._inline_think_buffer += delta
        buf = self._inline_think_buffer.lstrip()

        think_open_re = _re.compile(r"<(?:think|thinking)\s*>", _re.IGNORECASE)
        m = think_open_re.match(buf)
        if m:
            # Detected  open tag — switch to reasoning phase
            self._inline_think_phase = self._PHASE_REASONING
            events: list[bytes] = []
            # Anything after the open tag is reasoning content
            after_tag = buf[m.end():]
            if after_tag:
                events.extend(self.push_reasoning_delta(after_tag))
            self._inline_think_buffer = ""
            return events

        # Check if buffer could still be a partial  prefix
        if could_be_partial_think_open(buf):
            # Not enough data yet — keep buffering (silently)
            return []

        # Not a  prefix — flush entire buffer as text
        self._inline_think_phase = self._PHASE_TEXT
        events: list[bytes] = []
        if self._inline_think_buffer:
            events.extend(self.push_text_delta(self._inline_think_buffer))
            self._inline_think_buffer = ""
        return events

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

        # Flush inline think buffer: if still in detecting/reasoning phase,
        # treat buffered content as text (unclosed think block at stream end)
        if self._inline_think_phase == self._PHASE_REASONING:
            # Unclosed think block — treat accumulated reasoning as-is
            events.extend(self.finalize_reasoning_if_open())
        elif self._inline_think_phase == self._PHASE_DETECTING and self._inline_think_buffer:
            # Never saw a think tag — emit buffer as text
            events.extend(self.push_text_delta(self._inline_think_buffer))
            self._inline_think_buffer = ""
        self._inline_think_phase = self._PHASE_TEXT

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
        status = "incomplete" if self.envelope.finish_reason in ("length", "content_filter") else ("in_progress" if self.envelope.finish_reason == "tool_calls" else "completed")
        response = self.envelope.base_response(status, output)
        if status == "incomplete":
            reason = "content_filter" if self.envelope.finish_reason == "content_filter" else "max_output_tokens"
            response["incomplete_details"] = {"reason": reason}
        events.append(sse_event("response.completed", {"type": "response.completed", "response": response}))
        return events

    def fail(self, message: str, error_type: str = "stream_error") -> bytes:
        self.envelope.completed = True
        # Finalize sub-modules so their output items appear in the failed response
        self.reasoning.finalize(self.envelope)
        self.message.finalize(self.envelope)
        self.tools.finalize(self.envelope)
        response = self.envelope.base_response("failed", self.envelope.completed_output_items())
        response["error"] = {"message": message, "type": error_type}
        return sse_event("response.failed", {"type": "response.failed", "response": response})

    def build_assistant_message(self) -> ChatMessage | None:
        """从流状态构建 assistant ChatMessage，用于 session 持久化。

        Preserves full structured content (text + refusal parts) so that
        previous_response_id continuations see the complete history.
        """
        from .models import ChatMessage

        tool_call_states = {k: v for k, v in self.tools.tool_calls.items() if v.name}

        # Build content from finalized parts — includes both text and refusal
        has_parts = bool(self.message.parts)
        has_unstructured_text = bool(self.message.text) and not has_parts

        if not has_parts and not has_unstructured_text and not tool_call_states:
            if self.reasoning.text:
                # reasoning-only response: no visible content, keep empty content
                pass
            else:
                return None

        # If parts are present (stream was finalized), use structured content
        if has_parts:
            content: str | list[dict[str, Any]] | None = list(self.message.parts)
        elif has_unstructured_text:
            content = self.message.text or None
        else:
            content = None

        chat_tool_calls = None
        if tool_call_states:
            chat_tool_calls = []
            for index, state in sorted(tool_call_states.items(), key=lambda pair: pair[0]):
                chat_tool_calls.append({
                    "id": state.call_id or f"call_{index}",
                    "type": "function",
                    "function": {
                        "name": state.name,
                        "arguments": state.arguments,
                    },
                })

        reasoning = self.reasoning.text.strip() or None
        return ChatMessage(
            role="assistant",
            content=content,
            tool_calls=chat_tool_calls,
            reasoning_content=reasoning,
        )
