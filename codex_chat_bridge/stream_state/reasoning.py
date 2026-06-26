"""Reasoning 状态机 — 跟踪推理块的累积与最终确定。"""

from __future__ import annotations

from .envelope import ResponseEnvelopeState, sse_event


class ReasoningState:
    def __init__(self) -> None:
        self.text = ""
        self.item_added = False
        self.done = False
        self.output_index: int = 0

    def push_delta(self, envelope: ResponseEnvelopeState, delta: str) -> list[bytes]:
        if self.done:
            return []
        events: list[bytes] = []
        if not self.item_added:
            self.item_added = True
            self.output_index = envelope.allocate_output_index()
            events.append(
                sse_event(
                    "response.output_item.added",
                    {
                        "type": "response.output_item.added",
                        "output_index": self.output_index,
                        "item": {
                            "id": envelope.reasoning_item_id,
                            "type": "reasoning",
                            "status": "in_progress",
                            "summary": [],
                        },
                    },
                )
            )
            events.append(
                sse_event(
                    "response.reasoning_summary_part.added",
                    {
                        "type": "response.reasoning_summary_part.added",
                        "item_id": envelope.reasoning_item_id,
                        "output_index": self.output_index,
                        "summary_index": 0,
                        "part": {"type": "summary_text", "text": ""},
                    },
                )
            )
        self.text += delta
        events.append(
            sse_event(
                "response.reasoning_summary_text.delta",
                {
                    "type": "response.reasoning_summary_text.delta",
                    "item_id": envelope.reasoning_item_id,
                    "output_index": self.output_index,
                    "summary_index": 0,
                    "delta": delta,
                },
            )
        )
        return events

    def finalize(self, envelope: ResponseEnvelopeState) -> list[bytes]:
        if not self.item_added or self.done:
            return []
        self.done = True
        item = {
            "id": envelope.reasoning_item_id,
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": self.text}],
        }
        envelope.append_completed_item(self.output_index, item)
        return [
            sse_event(
                "response.reasoning_summary_text.done",
                {
                    "type": "response.reasoning_summary_text.done",
                    "item_id": envelope.reasoning_item_id,
                    "output_index": self.output_index,
                    "summary_index": 0,
                    "text": self.text,
                },
            ),
            sse_event(
                "response.reasoning_summary_part.done",
                {
                    "type": "response.reasoning_summary_part.done",
                    "item_id": envelope.reasoning_item_id,
                    "output_index": self.output_index,
                    "summary_index": 0,
                    "part": {"type": "summary_text", "text": self.text},
                },
            ),
            sse_event(
                "response.output_item.done",
                {
                    "type": "response.output_item.done",
                    "output_index": self.output_index,
                    "item": item,
                },
            ),
        ]

    def active_text_for_tools(self) -> str:
        return self.text.strip() if self.text and not self.done else ""
