from __future__ import annotations

import time

from ..protocol.sse import sse_event
from ..response_semantics import map_chat_usage, REQUEST_ECHO_FIELDS


class ResponseEnvelopeState:
    # Request-echo fields written into the final response per OpenAI spec
    # (imported from response_semantics to avoid duplication)

    def __init__(self, response_id: str | None = None) -> None:
        self.response_started = False
        self.completed = False
        self.response_id = response_id or "resp_bridge"
        self.model = ""
        self.created_at = int(time.time())
        self.status: str | None = None
        self.usage: dict | None = None
        self.finish_reason: str | None = None
        self.next_output_index = 0
        self.completed_items: list[tuple[int, dict]] = []
        self._request_echo: dict | None = None

    @property
    def message_item_id(self) -> str:
        # Some OpenAI-compatible validators (including NewAPI's Responses path)
        # require message item ids to begin with "msg" when the item is later
        # echoed back via previous_response_id continuation.
        return f"msg_{self.response_id}"

    @property
    def reasoning_item_id(self) -> str:
        return f"rs_{self.response_id}"

    def set_request_echo(self, original_request: dict | None) -> None:
        """Store the original Responses request for echo-back in finalize."""
        self._request_echo = original_request

    def _apply_request_echo(self, response: dict) -> None:
        """Write request-echo fields into the response dict."""
        if not self._request_echo:
            return
        for key in REQUEST_ECHO_FIELDS:
            value = self._request_echo.get(key)
            if value is not None:
                response[key] = value

    def allocate_output_index(self) -> int:
        idx = self.next_output_index
        self.next_output_index += 1
        return idx

    def base_response(self, status: str, output: list[dict]) -> dict:
        self.status = status
        response = {
            "id": self.response_id,
            "object": "response",
            "created_at": self.created_at,
            "status": status,
            "model": self.model,
            "output": output,
            "usage": self.usage or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        }
        self._apply_request_echo(response)
        return response

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
        if payload.get("model"):
            self.model = payload["model"]
        if payload.get("created"):
            self.created_at = payload["created"]
        if payload.get("usage"):
            self.usage = map_chat_usage(payload["usage"])
