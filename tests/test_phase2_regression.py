from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from codex_chat_bridge.api import response_service, routes
from codex_chat_bridge.api.policy import message_has_semantic_content
from codex_chat_bridge.api.routes import app
from codex_chat_bridge.bridge_context import BridgeToolContext
from codex_chat_bridge.config import Settings
from codex_chat_bridge.errors import InvalidRequestError, UnsupportedInputItemError
from codex_chat_bridge.models import ChatMessage, ResponsesRequest
from codex_chat_bridge.protocol.session import _assistant_message_from_chat_body
from codex_chat_bridge.stream_chat_to_responses import _chat_message_to_fake_delta
from codex_chat_bridge.stream_responses_state import ResponsesStreamState
from codex_chat_bridge.stream_state.envelope import ResponseEnvelopeState
from codex_chat_bridge.stream_state.message import MessageState
from codex_chat_bridge.stream_state.reasoning import ReasoningState


def _single_upstream_settings() -> Settings:
    return Settings(
        upstream_base_url="https://newapi.example.com/v1",
        upstream_api_key="test-key",
        upstream_timeout_seconds=30,
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


async def _collect_stream_chunks(response) -> list[bytes]:
    return [chunk async for chunk in response.body_iterator]


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
            "param": "{\"error\":{\"message\":\"catalog unavailable\"}}",
        }
    }


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
    with patch("codex_chat_bridge.api.response_service.get_settings", return_value=_single_upstream_settings()), patch(
        "codex_chat_bridge.api.response_service.UpstreamClient", FailingUpstreamClient,
    ):
        response = client.post("/v1/responses", json={"model": "test-model", "input": "hello"})

    body = response.json()
    assert response.status_code == 429
    assert body == {
        "error": {
            "message": "rate limited",
            "type": "upstream_error",
            "code": "upstream_request_failed",
            "param": "{\"error\":{\"message\":\"rate limited\"}}",
        }
    }


def test_audio_only_message_counts_as_semantic_content() -> None:
    message = ChatMessage(
        role="user",
        content=[{"type": "input_audio", "input_audio": {"url": "https://example.com/audio.wav"}}],
    )

    assert message_has_semantic_content(message) is True


def test_create_response_core_rejects_n_greater_than_one_before_upstream() -> None:
    payload = ResponsesRequest(model="test-model", input="hello", n=2)

    with patch("codex_chat_bridge.api.response_service.UpstreamClient", side_effect=AssertionError("UpstreamClient should not be created")):
        try:
            asyncio.run(response_service.create_response_core(payload))
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

    with patch("codex_chat_bridge.api.response_service.get_settings", return_value=_single_upstream_settings()), patch(
        "codex_chat_bridge.api.response_service.UpstreamClient", AcceptingUpstreamClient,
    ), patch("codex_chat_bridge.api.response_service.resolve_session", return_value=(None, None, None)), patch(
        "codex_chat_bridge.api.response_service.save_session", lambda *args, **kwargs: None,
    ):
        for n in (1, None):
            payload = ResponsesRequest(model="test-model", input="hello", n=n)
            response = asyncio.run(response_service.create_response_core(payload))
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
            "id": "msg_resp_regression",
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


def test_reasoning_state_tracks_active_text_and_finalizes_summary() -> None:
    envelope = ResponseEnvelopeState(response_id="resp_reasoning_regression")
    reasoning_state = ReasoningState()

    reasoning_state.push_delta(envelope, " Need ")
    reasoning_state.push_delta(envelope, "context. ")
    assert reasoning_state.active_text_for_tools() == "Need context."

    events = b"".join(reasoning_state.finalize(envelope)).decode()

    assert reasoning_state.active_text_for_tools() == ""
    assert "event: response.reasoning_summary_text.done" in events
    assert '"text": " Need context. "' in events


def test_stream_assistant_message_is_chat_compatible_for_session_replay() -> None:
    state = ResponsesStreamState(response_id="resp_stream_regression")

    state.push_text_delta("A")
    state.push_refusal_part("No")
    state.push_text_delta("B")
    state.finalize()

    assistant = state.build_assistant_message()
    assert assistant is not None
    assert assistant.role == "assistant"
    assert assistant.content == "A\n[refusal]: No\nB"


def test_stream_finalize_marks_tool_calls_as_in_progress() -> None:
    state = ResponsesStreamState(response_id="resp_tool_calls")
    state.push_tool_call_delta(
        {
            "index": 0,
            "id": "call_0",
            "function": {"name": "demo_tool", "arguments": "{\"x\":1}"},
        },
        None,
    )
    state.set_finish_reason("tool_calls")

    output = b"".join(state.finalize()).decode()

    assert 'event: response.completed' in output
    assert '"status": "in_progress"' in output


def test_stream_finalize_without_finish_reason_and_no_output_emits_failed_event() -> None:
    state = ResponsesStreamState(response_id="resp_truncated_empty")

    output = b"".join(state.finalize()).decode()

    assert "event: response.failed" in output
    assert '"type": "stream_truncated"' in output


def test_stream_fail_preserves_partial_output_items() -> None:
    state = ResponsesStreamState(response_id="resp_partial_fail")
    state.push_reasoning_delta("Need context.")
    state.push_text_delta("hello")

    output = b"".join(state.fail("bad request", "invalid_request_error")).decode()

    assert "event: response.failed" in output
    assert '"type": "message"' in output
    assert '"type": "reasoning"' in output
    assert '"message": "bad request"' in output


def test_response_envelope_preserves_bridge_response_id_when_metadata_has_upstream_id() -> None:
    envelope = ResponseEnvelopeState(response_id="resp_bridge_abc123")

    envelope.apply_metadata({"id": "chatcmpl_upstream", "model": "test-model", "created": 1710000000})

    assert envelope.response_id == "resp_bridge_abc123"
    assert envelope.model == "test-model"
    assert envelope.created_at == 1710000000


def test_response_envelope_completed_event_applies_finish_reason_mapping_and_request_echo() -> None:
    envelope = ResponseEnvelopeState(response_id="resp_bridge_echo")
    envelope.set_request_echo({"instructions": "be terse", "metadata": {"trace": "abc"}})
    envelope.finish_reason = "content_filter"
    envelope.apply_metadata({"model": "test-model", "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}})

    output = envelope.completed_event([{"id": "item_1", "type": "message"}]).decode()

    assert 'event: response.completed' in output
    assert '"status": "incomplete"' in output
    assert '"incomplete_details": {"reason": "content_filter"}' in output
    assert '"instructions": "be terse"' in output
    assert '"metadata": {"trace": "abc"}' in output


def test_chat_message_to_fake_delta_injects_indexes_for_parallel_tool_calls() -> None:
    delta = _chat_message_to_fake_delta(
        {
            "message": {
                "role": "assistant",
                "tool_calls": [
                    {"id": "call_a", "type": "function", "function": {"name": "alpha", "arguments": "{}"}},
                    {"id": "call_b", "type": "function", "function": {"name": "beta", "arguments": "{}"}},
                ],
            }
        }
    )

    assert delta["tool_calls"][0]["index"] == 0
    assert delta["tool_calls"][1]["index"] == 1


def test_stream_upstream_streaming_does_not_persist_failed_streams() -> None:
    class DummyClient:
        def stream_chat_completion(self, payload):
            async def _unused():
                if False:
                    yield b""

            return _unused()

    class FakeState:
        def __init__(self, status: str) -> None:
            self.envelope = SimpleNamespace(completed=True, status=status)

        def build_assistant_message(self) -> ChatMessage:
            return ChatMessage(role="assistant", content="partial")

    async def fake_stream(*args, _captured_state=None, **kwargs):
        if _captured_state is not None:
            _captured_state.append(FakeState("failed"))
        yield b"data: {\"type\": \"response.failed\"}\n\n"

    saves: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    chat_request = SimpleNamespace(messages=[], model="test-model")

    deps = response_service.ServiceDependencies(
        save_session=lambda *args, **kwargs: saves.append((args, kwargs)),
    )

    with patch("codex_chat_bridge.api.response_service.create_responses_sse_stream_from_chat_stream", fake_stream):
        response = asyncio.run(
            response_service._stream_upstream_streaming(
                DummyClient(),
                chat_request,
                BridgeToolContext(),
                "resp_bridge_failed",
                deps=deps,
            )
        )
        asyncio.run(_collect_stream_chunks(response))

    assert saves == []


def test_stream_upstream_streaming_persists_successful_streams() -> None:
    class DummyClient:
        def stream_chat_completion(self, payload):
            async def _unused():
                if False:
                    yield b""

            return _unused()

    assistant_message = ChatMessage(role="assistant", content="ok")

    class FakeState:
        def __init__(self, status: str) -> None:
            self.envelope = SimpleNamespace(completed=True, status=status)

        def build_assistant_message(self) -> ChatMessage:
            return assistant_message

    async def fake_stream(*args, _captured_state=None, **kwargs):
        if _captured_state is not None:
            _captured_state.append(FakeState("completed"))
        yield b"data: {\"type\": \"response.completed\"}\n\n"

    saves: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    chat_request = SimpleNamespace(messages=[], model="test-model")

    deps = response_service.ServiceDependencies(
        save_session=lambda *args, **kwargs: saves.append((args, kwargs)),
    )

    with patch("codex_chat_bridge.api.response_service.create_responses_sse_stream_from_chat_stream", fake_stream):
        response = asyncio.run(
            response_service._stream_upstream_streaming(
                DummyClient(),
                chat_request,
                BridgeToolContext(),
                "resp_bridge_completed",
                deps=deps,
            )
        )
        asyncio.run(_collect_stream_chunks(response))

    assert len(saves) == 1
    args, kwargs = saves[0]
    assert args[0] == "resp_bridge_completed"
    assert kwargs["assistant_message"] == assistant_message


def test_assistant_message_from_chat_body_preserves_refusal_only_turns() -> None:
    message = _assistant_message_from_chat_body(
        {"choices": [{"message": {"role": "assistant", "refusal": "No."}}]}
    )

    assert message is not None
    assert message.role == "assistant"
    # Refusal is preserved with a typed prefix so it survives session replay
    # without semantically conflating it with normal assistant content.
    assert message.content == "[refusal]: No."
    assert message.reasoning_content is None


def test_assistant_message_from_chat_body_preserves_reasoning_only_turns() -> None:
    message = _assistant_message_from_chat_body(
        {"choices": [{"message": {"role": "assistant", "reasoning_content": "thinking"}}]}
    )

    assert message is not None
    assert message.role == "assistant"
    # When only reasoning_content exists (no content, refusal, or tool_calls),
    # content is None rather than empty string.
    assert message.content is None
    assert message.reasoning_content == "thinking"


def test_hosted_tool_passthrough_policy_keeps_raw_tool() -> None:
    context = BridgeToolContext()
    tool = {"type": "web_search", "search_context_size": "low"}

    with patch(
        "codex_chat_bridge.bridge_context.context.get_settings",
        return_value=Settings(unsupported_tool_policy="passthrough"),
    ):
        context.add_response_tool(tool)

    assert context.chat_tools == [tool]


def test_hosted_tool_reject_policy_raises_error() -> None:
    context = BridgeToolContext()

    with patch(
        "codex_chat_bridge.bridge_context.context.get_settings",
        return_value=Settings(unsupported_tool_policy="reject"),
    ):
        with pytest.raises(UnsupportedInputItemError):
            context.add_response_tool({"type": "web_search"})


def test_hosted_tool_ignore_policy_skips_tool() -> None:
    context = BridgeToolContext()

    with patch(
        "codex_chat_bridge.bridge_context.context.get_settings",
        return_value=Settings(unsupported_tool_policy="ignore"),
    ):
        context.add_response_tool({"type": "web_search"})

    assert context.chat_tools == []
