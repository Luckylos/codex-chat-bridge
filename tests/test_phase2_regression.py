from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from codex_chat_bridge.api import routes
from codex_chat_bridge.api.policy import message_has_semantic_content
from codex_chat_bridge.api.routes import app
from codex_chat_bridge.config import Settings
from codex_chat_bridge.errors import InvalidRequestError
from codex_chat_bridge.models import ChatMessage, ResponsesRequest
from codex_chat_bridge.stream_state.envelope import ResponseEnvelopeState
from codex_chat_bridge.stream_state.message import MessageState


def _single_upstream_settings() -> Settings:
    return Settings(
        upstream_base_url="https://newapi.example.com/v1",
        upstream_api_key="test-key",
        upstream_timeout_seconds=30,
        public_base_url="http://127.0.0.1:18090/v1",
    )


def _http_status_error(
    method: str,
    url: str,
    status_code: int,
    payload: dict,
) -> httpx.HTTPStatusError:
    request = httpx.Request(method, url)
    response = httpx.Response(status_code, request=request, json=payload)
    return httpx.HTTPStatusError(f"HTTP {status_code}", request=request, response=response)


def test_health_reads_upstream_reachable_from_request_app_state_dynamically() -> None:
    state = SimpleNamespace(health_upstream_reachable=True)
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    health_handler = cast(Any, routes.health)

    first = asyncio.run(health_handler(request))
    state.health_upstream_reachable = False
    second = asyncio.run(health_handler(request))

    assert first["upstream_reachable"] is True
    assert second["upstream_reachable"] is False


def test_models_http_status_error_uses_bridge_error_envelope() -> None:
    class FailingUpstreamClient:
        def __init__(self, settings) -> None:
            self.settings = settings

        async def list_models(self):
            raise _http_status_error(
                "GET",
                "https://newapi.example.com/v1/models",
                503,
                {"error": {"message": "catalog unavailable"}},
            )

    client = TestClient(app)
    with patch("codex_chat_bridge.api.routes.get_settings", return_value=_single_upstream_settings()), patch(
        "codex_chat_bridge.api.routes.UpstreamClient", FailingUpstreamClient,
    ):
        response = client.get("/v1/models")

    body = response.json()
    assert response.status_code == 503
    assert body == {
        "error": {
            "message": "catalog unavailable",
            "type": "upstream_error",
            "code": "upstream_models_unavailable",
        }
    }
    assert "detail" not in body


def test_create_response_http_status_error_uses_bridge_error_envelope() -> None:
    class FailingUpstreamClient:
        def __init__(self, settings) -> None:
            self.settings = settings

        async def create_chat_completion(self, payload):
            raise _http_status_error(
                "POST",
                "https://newapi.example.com/v1/chat/completions",
                429,
                {"error": {"message": "rate limited"}},
            )

    client = TestClient(app)
    with patch("codex_chat_bridge.api.routes.get_settings", return_value=_single_upstream_settings()), patch(
        "codex_chat_bridge.api.routes.UpstreamClient", FailingUpstreamClient,
    ):
        response = client.post("/v1/responses", json={"model": "test-model", "input": "hello"})

    body = response.json()
    assert response.status_code == 429
    assert body == {
        "error": {
            "message": "rate limited",
            "type": "upstream_error",
            "code": "upstream_request_failed",
        }
    }
    assert "detail" not in body


def test_audio_only_message_counts_as_semantic_content() -> None:
    message = ChatMessage(
        role="user",
        content=[{"type": "input_audio", "input_audio": {"url": "https://example.com/audio.wav"}}],
    )

    assert message_has_semantic_content(message) is True


def test_create_response_core_rejects_n_greater_than_one_before_upstream() -> None:
    payload = ResponsesRequest(model="test-model", input="hello", n=2)

    with patch("codex_chat_bridge.api.routes.UpstreamClient", side_effect=AssertionError("UpstreamClient should not be created")):
        try:
            asyncio.run(routes._create_response_core(payload))
        except InvalidRequestError as exc:
            assert exc.code == "unsupported_n"
            assert exc.status_code == 400
        else:
            raise AssertionError("Expected InvalidRequestError for n > 1")


def test_create_response_core_accepts_n_one_and_none() -> None:
    class AcceptingUpstreamClient:
        def __init__(self, settings) -> None:
            self.settings = settings

        async def create_chat_completion(self, payload):
            return {
                "id": "chatcmpl_regression",
                "object": "chat.completion",
                "created": 1710000000,
                "model": payload.model,
                "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

    with patch("codex_chat_bridge.api.routes.get_settings", return_value=_single_upstream_settings()), patch(
        "codex_chat_bridge.api.routes.UpstreamClient", AcceptingUpstreamClient,
    ), patch("codex_chat_bridge.api.routes.resolve_session", return_value=(None, None, None)), patch(
        "codex_chat_bridge.api.routes.save_session", lambda *args, **kwargs: None,
    ):
        for n in (1, None):
            payload = ResponsesRequest(model="test-model", input="hello", n=n)
            response = asyncio.run(routes._create_response_core(payload))
            assert response.status_code == 200


def test_message_state_preserves_text_refusal_text_part_order_on_finalize() -> None:
    envelope = ResponseEnvelopeState(response_id="resp_regression")
    message_state = MessageState()

    message_state.push_text_delta(envelope, "A")
    message_state.push_refusal_part(envelope, "No")
    message_state.push_text_delta(envelope, "B")
    message_state.finalize(envelope)

    completed_items = envelope.completed_output_items()
    assert completed_items == [
        {
            "id": "resp_regression_msg",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [
                {"type": "output_text", "text": "A", "annotations": []},
                {"type": "refusal", "refusal": "No"},
                {"type": "output_text", "text": "B", "annotations": []},
            ],
        }
    ]
