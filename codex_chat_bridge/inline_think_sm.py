"""Inline-think three-phase state machine for streaming content deltas.

Extracted from ResponsesStreamState so that MessageState remains
focused on text/refusal/annotation tracking, while the inline-think
detection and routing logic is independently testable.

Phases:
  DETECTING → buffering to check for a  prefix
  REASONING → inside a  block, routing to reasoning
  TEXT       → past the think block, normal text emission
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .stream_state import ResponsesStreamState


class InlineThinkStateMachine:
    """Encapsulates the three-phase inline think detection logic.

    Extracted from ResponsesStreamState so that MessageState remains
    focused on text/refusal/annotation tracking, while inline-think
    routing is independently testable and maintainable.
    """

    PHASE_DETECTING = "detecting"
    PHASE_REASONING = "reasoning"
    PHASE_TEXT = "text"

    def __init__(self) -> None:
        self._phase: str = self.PHASE_DETECTING
        self._buffer: str = ""

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def buffer(self) -> str:
        return self._buffer

    def is_text_phase(self) -> bool:
        return self._phase == self.PHASE_TEXT

    def is_detecting_or_reasoning(self) -> bool:
        return self._phase != self.PHASE_TEXT

    def push_content_delta(self, delta: str, state: ResponsesStreamState) -> list[bytes]:
        """Route a content delta through inline-think detection.

        If the delta starts with (or continues) a  block, route
        reasoning content to the reasoning state machine; once the
        think block closes, switch to normal text emission.
        """
        from .chat_to_responses.inline_think import could_be_partial_think_open

        if self._phase == self.PHASE_TEXT:
            # Already past the think block — emit as plain text
            return state.push_text_delta(delta)

        if self._phase == self.PHASE_REASONING:
            # Inside a  think block — look for closing tag
            close_re = re.compile(r"</(?:think|thinking)\s*>", re.IGNORECASE)
            m = close_re.search(delta)
            if m:
                # Close tag found: emit pre-close as reasoning, switch to text
                events: list[bytes] = []
                pre = delta[:m.start()]
                if pre:
                    events.extend(state.push_reasoning_delta(pre))
                events.extend(state.finalize_reasoning_if_open())
                self._phase = self.PHASE_TEXT
                post = delta[m.end():]
                if post:
                    events.extend(state.push_text_delta(post))
                return events
            # No close tag yet — entire delta is reasoning
            return state.push_reasoning_delta(delta)

        # Phase: DETECTING — accumulate to check for  prefix
        self._buffer += delta
        buf = self._buffer.lstrip()

        think_open_re = re.compile(r"<(?:think|thinking)\s*>", re.IGNORECASE)
        m = think_open_re.match(buf)
        if m:
            # Detected  open tag — switch to reasoning phase
            self._phase = self.PHASE_REASONING
            events: list[bytes] = []
            # Anything after the open tag is reasoning content
            after_tag = buf[m.end():]
            if after_tag:
                events.extend(state.push_reasoning_delta(after_tag))
            self._buffer = ""
            return events

        # Check if buffer could still be a partial  prefix
        if could_be_partial_think_open(buf):
            # Not enough data yet — keep buffering (silently)
            return []

        # Not a  prefix — flush entire buffer as text
        self._phase = self.PHASE_TEXT
        events: list[bytes] = []
        if self._buffer:
            events.extend(state.push_text_delta(self._buffer))
            self._buffer = ""
        return events

    def flush_on_finalize(self, state: ResponsesStreamState) -> list[bytes]:
        """Flush buffered content during stream finalization.

        - REASONING phase: finalize the reasoning state (unclosed think block)
        - DETECTING phase with non-empty buffer: emit buffer as text
        """
        events: list[bytes] = []
        if self._phase == self.PHASE_REASONING:
            # Unclosed think block — treat accumulated reasoning as-is
            events.extend(state.finalize_reasoning_if_open())
        elif self._phase == self.PHASE_DETECTING and self._buffer:
            # Never saw a think tag — emit buffer as text
            events.extend(state.push_text_delta(self._buffer))
            self._buffer = ""
        self._phase = self.PHASE_TEXT
        return events

    def force_to_text(self, state: ResponsesStreamState) -> list[bytes]:
        """Force-flush any buffered/reasoning content as text when tool calls arrive.

        Used when the stream switches to tool_calls before think detection completes.
        """
        events: list[bytes] = []
        if self._phase != self.PHASE_TEXT:
            events.extend(state.finalize_reasoning_if_open())
            if self._buffer:
                events.extend(state.push_text_delta(self._buffer))
                self._buffer = ""
            self._phase = self.PHASE_TEXT
        return events
