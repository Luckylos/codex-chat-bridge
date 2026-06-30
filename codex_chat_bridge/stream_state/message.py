from __future__ import annotations

from typing import Any

from .envelope import ResponseEnvelopeState, sse_event


class MessageState:
    def __init__(self) -> None:
        self.segments: list[dict[str, Any]] = []
        self.item_added = False
        self.item_done = False
        self.output_index: int | None = None
        self.parts: list[dict] = []
        self.text_part_done: set[int] = set()
        self._annotations: list[dict] = []

    @property
    def text(self) -> str:
        return "".join(
            segment["text"]
            for segment in self.segments
            if segment.get("type") == "output_text"
        )

    def add_annotations(self, annotations: list[dict] | None) -> None:
        """Accumulate annotations for the current or next text segment."""
        if not isinstance(annotations, list):
            return

        normalized = [annotation for annotation in annotations if isinstance(annotation, dict)]
        if not normalized:
            return

        current_segment = self._current_text_segment()
        if current_segment is not None:
            current_segment["annotations"].extend(normalized)
            return

        self._annotations.extend(normalized)

    def _current_text_segment(self) -> dict[str, Any] | None:
        if not self.segments:
            return None
        last_segment = self.segments[-1]
        if last_segment.get("type") != "output_text":
            return None
        if last_segment.get("content_index", -1) in self.text_part_done:
            return None
        return last_segment

    def _drain_pending_annotations(self) -> list[dict]:
        annotations = list(self._annotations)
        self._annotations.clear()
        return annotations

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

    def _start_text_segment(self, envelope: ResponseEnvelopeState) -> tuple[dict[str, Any], list[bytes]]:
        events = self._ensure_message_item_started(envelope)
        content_index = len(self.parts)
        annotations = self._drain_pending_annotations()
        part = {"type": "output_text", "text": "", "annotations": annotations}
        segment = {
            "type": "output_text",
            "content_index": content_index,
            "text": "",
            "annotations": annotations,
            "part": part,
        }
        self.segments.append(segment)
        self.parts.append(part)
        events.append(
            sse_event(
                "response.content_part.added",
                {
                    "type": "response.content_part.added",
                    "item_id": envelope.message_item_id,
                    "output_index": self.output_index,
                    "content_index": content_index,
                    "part": {"type": "output_text", "text": "", "annotations": annotations},
                },
            )
        )
        return segment, events

    def push_text_delta(self, envelope: ResponseEnvelopeState, delta: str) -> list[bytes]:
        if self.item_done or not delta:
            return []

        segment = self._current_text_segment()
        if segment is None:
            segment, events = self._start_text_segment(envelope)
        else:
            events = self._ensure_message_item_started(envelope)
            if self._annotations:
                segment["annotations"].extend(self._drain_pending_annotations())

        segment["text"] += delta
        events.append(
            sse_event(
                "response.output_text.delta",
                {
                    "type": "response.output_text.delta",
                    "item_id": envelope.message_item_id,
                    "output_index": self.output_index,
                    "content_index": segment["content_index"],
                    "delta": delta,
                },
            )
        )
        return events

    def push_refusal_part(self, envelope: ResponseEnvelopeState, refusal: str) -> list[bytes]:
        if not refusal or self.item_done:
            return []

        events = self._ensure_message_item_started(envelope)
        content_index = len(self.parts)
        part = {"type": "refusal", "refusal": refusal}
        segment = {
            "type": "refusal",
            "content_index": content_index,
            "part": part,
        }
        self.segments.append(segment)
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

    def content_parts(self) -> list[dict]:
        if self.item_done:
            return list(self.parts)

        rendered_parts: list[dict] = []
        for segment in self.segments:
            if segment.get("type") == "output_text":
                rendered_parts.append(
                    {
                        "type": "output_text",
                        "text": segment["text"],
                        "annotations": list(segment["annotations"]),
                    }
                )
            else:
                rendered_parts.append(dict(segment["part"]))
        return rendered_parts

    def finalize(self, envelope: ResponseEnvelopeState) -> list[bytes]:
        if not self.item_added or self.item_done:
            return []

        self.item_done = True
        events: list[bytes] = []
        for segment in self.segments:
            if segment.get("type") != "output_text":
                continue

            content_index = segment["content_index"]
            if content_index in self.text_part_done:
                continue

            self.text_part_done.add(content_index)
            text_part = {
                "type": "output_text",
                "text": segment["text"],
                "annotations": list(segment["annotations"]),
            }
            self.parts[content_index] = text_part
            segment["part"] = text_part
            events.append(
                sse_event(
                    "response.output_text.done",
                    {
                        "type": "response.output_text.done",
                        "item_id": envelope.message_item_id,
                        "output_index": self.output_index,
                        "content_index": content_index,
                        "text": segment["text"],
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
