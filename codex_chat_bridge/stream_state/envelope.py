from __future__ import annotations

import json
import time

from ..response_semantics import map_chat_usage


def sse_event(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


class ResponseEnvelopeState:
    def __init__(self) -> None:
        self.response_started = False
        self.completed = False
        self.response_id = "resp_bridge"
        self.model = ""
        self.created_at = int(time.time())
        self.usage: dict | None = None
        self.finish_reason: str | None = None
        self.next_output_index = 0
        self.completed_items: list[tuple[int, dict]] = []

    @property
    def message_item_id(self) -> str:
        return f"{self.response_id}_msg"

    @property
    def reasoning_item_id(self) -> str:
        return f"rs_{self.response_id}"

    def allocate_output_index(self) -> int:
        idx = self.next_output_index
        self.next_output_index += 1
        return idx

    def base_response(self, status: str, output: list[dict]) -> dict:
        return {
            "id": self.response_id,
            "object": "response",
            "created_at": self.created_at,
            "status": status,
            "model": self.model,
            "output": output,
            "usage": self.usage or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        }

    def ensure_started(self) -> list[bytes]:
        if self.response_started:
            return []
        self.response_started = True
        response = self.base_response("in_progress", [])
        return [
            sse_event("response.created", {"type": "response.created", "response": response}),
            sse_event("response.in_progress", {"type": "response.in_progress", "response": response}),
        ]

    def append_completed_item(self, output_index: int, item: dict) -> None:
        self.completed_items.append((output_index, item))

    def completed_output_items(self) -> list[dict]:
        return [item for _, item in sorted(self.completed_items, key=lambda pair: pair[0])]

    def apply_metadata(self, payload: dict) -> None:
        if payload.get("id"):
            self.response_id = f"resp_{payload['id']}"
        if payload.get("model"):
            self.model = payload["model"]
        if payload.get("created"):
            self.created_at = payload["created"]
        if payload.get("usage"):
            self.usage = map_chat_usage(payload["usage"])


class ReasoningState:
    def __init__(self) -> None:
        self.text = ""
        self.item_added = False
        self.done = False
        self.output_index: int | None = None

    def push_delta(self, envelope: ResponseEnvelopeState, delta: str) -> list[bytes]:
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
        envelope.append_completed_item(self.output_index or 0, item)
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
