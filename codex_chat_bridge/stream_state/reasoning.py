"""Reasoning state machine — tracks accumulation and finalization of reasoning blocks."""

from __future__ import annotations

from .envelope import ResponseEnvelopeState
from .tool_events import (
    output_item_added,
    output_item_done,
    reasoning_summary_part_added,
    reasoning_summary_part_done,
    reasoning_summary_text_delta,
    reasoning_summary_text_done,
)


class ReasoningState:
    def __init__(self) -> None:
        self.text = ""
        self.item_added = False
        self.done = False
        self.output_index: int = 0

    def _reasoning_item_in_progress(self, envelope: ResponseEnvelopeState) -> dict:
        return {
            "id": envelope.reasoning_item_id,
            "type": "reasoning",
            "status": "in_progress",
            "summary": [],
        }

    def _reasoning_item_completed(self, envelope: ResponseEnvelopeState) -> dict:
        return {
            "id": envelope.reasoning_item_id,
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": self.text}],
        }

    def _ensure_started(self, envelope: ResponseEnvelopeState) -> list[bytes]:
        if self.item_added:
            return []
        self.item_added = True
        self.output_index = envelope.allocate_output_index()
        return [
            output_item_added(self.output_index, self._reasoning_item_in_progress(envelope)),
            reasoning_summary_part_added(
                envelope.reasoning_item_id,
                self.output_index,
                0,
                {"type": "summary_text", "text": ""},
            ),
        ]

    def push_delta(self, envelope: ResponseEnvelopeState, delta: str) -> list[bytes]:
        if self.done:
            return []
        events = self._ensure_started(envelope)
        self.text += delta
        events.append(
            reasoning_summary_text_delta(
                envelope.reasoning_item_id,
                self.output_index,
                0,
                delta,
            )
        )
        return events

    def finalize(self, envelope: ResponseEnvelopeState) -> list[bytes]:
        if not self.item_added or self.done:
            return []
        self.done = True
        item = self._reasoning_item_completed(envelope)
        summary_part = {"type": "summary_text", "text": self.text}
        envelope.append_completed_item(self.output_index, item)
        return [
            reasoning_summary_text_done(
                envelope.reasoning_item_id,
                self.output_index,
                0,
                self.text,
            ),
            reasoning_summary_part_done(
                envelope.reasoning_item_id,
                self.output_index,
                0,
                summary_part,
            ),
            output_item_done(self.output_index, item),
        ]

    def active_text_for_tools(self) -> str:
        return self.text.strip() if self.text and not self.done else ""
