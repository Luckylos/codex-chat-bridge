from __future__ import annotations

from .envelope import ResponseEnvelopeState, sse_event


class MessageState:
    def __init__(self) -> None:
        self.text = ""
        self.item_added = False
        self.item_done = False
        self.output_index: int | None = None
        self.parts: list[dict] = []
        self.text_content_index: int | None = None
        self.text_part_done = False

    def _ensure_message_item_started(self, envelope: ResponseEnvelopeState) -> list[bytes]:
        if self.item_added:
            return []
        self.item_added = True
        self.output_index = envelope.allocate_output_index()
        return [
            sse_event(
                "response.output_item.added",
                {
                    "type": "response.output_item.added",
                    "output_index": self.output_index,
                    "item": {
                        "id": envelope.message_item_id,
                        "type": "message",
                        "status": "in_progress",
                        "role": "assistant",
                        "content": [],
                    },
                },
            )
        ]

    def _ensure_text_part_started(self, envelope: ResponseEnvelopeState) -> list[bytes]:
        events = self._ensure_message_item_started(envelope)
        if self.text_content_index is not None:
            return events
        self.text_content_index = len(self.parts)
        self.parts.append({"type": "output_text", "text": "", "annotations": []})
        events.append(
            sse_event(
                "response.content_part.added",
                {
                    "type": "response.content_part.added",
                    "item_id": envelope.message_item_id,
                    "output_index": self.output_index,
                    "content_index": self.text_content_index,
                    "part": {"type": "output_text", "text": "", "annotations": []},
                },
            )
        )
        return events

    def push_text_delta(self, envelope: ResponseEnvelopeState, delta: str) -> list[bytes]:
        events = self._ensure_text_part_started(envelope)
        self.text += delta
        events.append(
            sse_event(
                "response.output_text.delta",
                {
                    "type": "response.output_text.delta",
                    "item_id": envelope.message_item_id,
                    "output_index": self.output_index,
                    "content_index": self.text_content_index,
                    "delta": delta,
                },
            )
        )
        return events

    def push_refusal_part(self, envelope: ResponseEnvelopeState, refusal: str) -> list[bytes]:
        if not refusal:
            return []
        events = self._ensure_message_item_started(envelope)
        content_index = len(self.parts)
        part = {"type": "refusal", "refusal": refusal}
        self.parts.append(part)
        events.append(
            sse_event(
                "response.content_part.added",
                {
                    "type": "response.content_part.added",
                    "item_id": envelope.message_item_id,
                    "output_index": self.output_index,
                    "content_index": content_index,
                    "part": part,
                },
            )
        )
        events.append(
            sse_event(
                "response.content_part.done",
                {
                    "type": "response.content_part.done",
                    "item_id": envelope.message_item_id,
                    "output_index": self.output_index,
                    "content_index": content_index,
                    "part": part,
                },
            )
        )
        return events

    def finalize(self, envelope: ResponseEnvelopeState) -> list[bytes]:
        if not self.item_added or self.item_done:
            return []
        self.item_done = True
        events: list[bytes] = []
        if self.text_content_index is not None and not self.text_part_done:
            self.text_part_done = True
            text_part = {"type": "output_text", "text": self.text, "annotations": []}
            self.parts[self.text_content_index] = text_part
            events.append(
                sse_event(
                    "response.output_text.done",
                    {
                        "type": "response.output_text.done",
                        "item_id": envelope.message_item_id,
                        "output_index": self.output_index,
                        "content_index": self.text_content_index,
                        "text": self.text,
                    },
                )
            )
            events.append(
                sse_event(
                    "response.content_part.done",
                    {
                        "type": "response.content_part.done",
                        "item_id": envelope.message_item_id,
                        "output_index": self.output_index,
                        "content_index": self.text_content_index,
                        "part": text_part,
                    },
                )
            )
        item = {
            "id": envelope.message_item_id,
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": list(self.parts),
        }
        envelope.append_completed_item(self.output_index or 0, item)
        events.append(
            sse_event(
                "response.output_item.done",
                {
                    "type": "response.output_item.done",
                    "output_index": self.output_index,
                    "item": item,
                },
            )
        )
        return events
