from __future__ import annotations

import asyncio
import json
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
from codex_chat_bridge.stream_chat_to_responses import (
    _chat_message_to_fake_delta,
    create_responses_sse_from_chat_response,
    create_responses_sse_stream_from_chat_stream,
)
from codex_chat_bridge.stream_responses_state import ResponsesStreamState
from codex_chat_bridge.stream_state.envelope import ResponseEnvelopeState
from codex_chat_bridge.stream_state.message import MessageState
from codex_chat_bridge.stream_state.reasoning import ReasoningState
from codex_chat_bridge.stream_state.tools import ToolStateStore


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


def test_message_state_preserves_pending_annotations_in_completed_item() -> None:
    envelope = ResponseEnvelopeState(response_id="resp_annotations")
    message_state = MessageState()

    message_state.add_annotations([{"type": "url_citation", "url": "https://example.com"}])
    message_state.push_text_delta(envelope, "Hello")
    message_state.finalize(envelope)

    assert envelope.completed_output_items() == [
        {
            "id": "msg_resp_annotations",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": "Hello",
                    "annotations": [{"type": "url_citation", "url": "https://example.com"}],
                }
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


def test_reasoning_state_appends_completed_item_to_envelope() -> None:
    envelope = ResponseEnvelopeState(response_id="resp_reasoning_item")
    reasoning_state = ReasoningState()

    reasoning_state.push_delta(envelope, "Need context.")
    reasoning_state.finalize(envelope)

    assert envelope.completed_output_items() == [
        {
            "id": "rs_resp_reasoning_item",
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "Need context."}],
        }
    ]


def test_stream_accepts_reasoning_field_alias_in_chat_delta() -> None:
    async def upstream_stream():
        payload = {
            "id": "chatcmpl_reasoning_alias",
            "model": "demo-model",
            "choices": [{"delta": {"reasoning": "Need context."}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    async def collect() -> str:
        parts: list[str] = []
        async for chunk in create_responses_sse_stream_from_chat_stream(upstream_stream()):
            parts.append(chunk.decode())
        return "".join(parts)

    output = asyncio.run(collect())
    compact = output.replace(" ", "")
    assert "event: response.reasoning_summary_text.done" in output
    assert '"text":"Needcontext."' in compact


def _response_payload_for_event(sse_output: str, event_name: str) -> dict[str, Any]:
    for block in sse_output.split("\n\n"):
        if f"event: {event_name}" not in block:
            continue
        data_line = next((line for line in block.splitlines() if line.startswith("data: ")), None)
        if data_line is None:
            continue
        payload = json.loads(data_line[6:])
        return payload["response"]
    raise AssertionError(f"{event_name} event not found")


def _completed_response_output(sse_output: str) -> list[dict[str, Any]]:
    return _response_payload_for_event(sse_output, "response.completed")["output"]


def test_buffered_chat_response_preserves_message_before_tool_call_in_mixed_output() -> None:
    chat_body = {
        "id": "chatcmpl_buffered_mixed_output",
        "model": "demo-model",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "hello",
                    "reasoning_content": "Need tool.",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "shell", "arguments": '{"command":"pwd"}'},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }

    async def collect() -> str:
        parts: list[str] = []
        async for chunk in create_responses_sse_from_chat_response(chat_body):
            parts.append(chunk.decode())
        return "".join(parts)

    output = asyncio.run(collect())
    completed_output = _completed_response_output(output)

    assert [item["type"] for item in completed_output] == ["reasoning", "message", "function_call"]
    assert completed_output[2]["reasoning_content"] == "Need tool."


def test_stream_chunk_preserves_message_before_tool_call_in_mixed_output() -> None:
    async def upstream_stream():
        payload = {
            "id": "chatcmpl_stream_mixed_output",
            "model": "demo-model",
            "choices": [
                {
                    "delta": {
                        "reasoning_content": "Need tool.",
                        "content": "hello",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "shell", "arguments": '{"command":"pwd"}'},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    async def collect() -> str:
        parts: list[str] = []
        async for chunk in create_responses_sse_stream_from_chat_stream(upstream_stream()):
            parts.append(chunk.decode())
        return "".join(parts)

    output = asyncio.run(collect())
    completed_output = _completed_response_output(output)

    assert [item["type"] for item in completed_output] == ["reasoning", "message", "function_call"]
    assert completed_output[2]["reasoning_content"] == "Need tool."


def test_buffered_chat_response_restores_flat_namespace_function_call_in_mixed_output() -> None:
    tool_context = BridgeToolContext()
    tool_context.add_namespace_tool(
        {
            "type": "namespace",
            "name": "codex",
            "strategy": "flat",
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "shell",
                        "parameters": {
                            "type": "object",
                            "properties": {"command": {"type": "string"}},
                            "required": ["command"],
                        },
                    },
                }
            ],
        }
    )
    chat_body = {
        "id": "chatcmpl_buffered_flat_namespace_mixed",
        "model": "demo-model",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "hello",
                    "reasoning_content": "Need shell.",
                    "tool_calls": [
                        {
                            "id": "call_flat_1",
                            "type": "function",
                            "function": {"name": "codex__shell", "arguments": '{"command":"pwd"}'},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }

    async def collect() -> str:
        parts: list[str] = []
        async for chunk in create_responses_sse_from_chat_response(chat_body, tool_context=tool_context):
            parts.append(chunk.decode())
        return "".join(parts)

    output = asyncio.run(collect())
    completed_output = _completed_response_output(output)

    assert [item["type"] for item in completed_output] == ["reasoning", "message", "function_call"]
    assert completed_output[2]["name"] == "shell"
    assert completed_output[2]["namespace"] == "codex"
    assert completed_output[2]["reasoning_content"] == "Need shell."


def test_stream_chunk_restores_nested_namespace_function_call_in_mixed_output() -> None:
    tool_context = BridgeToolContext()
    tool_context.add_namespace_tool(
        {
            "type": "namespace",
            "name": "codex",
            "strategy": "nested_oneof",
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "shell",
                        "parameters": {
                            "type": "object",
                            "properties": {"command": {"type": "string"}},
                            "required": ["command"],
                        },
                    },
                }
            ],
        }
    )

    async def upstream_stream():
        payload = {
            "id": "chatcmpl_stream_nested_namespace_mixed",
            "model": "demo-model",
            "choices": [
                {
                    "delta": {
                        "reasoning_content": "Need shell.",
                        "content": "hello",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_nested_1",
                                "type": "function",
                                "function": {"name": "codex__codex", "arguments": '{"action":"shell","command":"pwd"}'},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    async def collect() -> str:
        parts: list[str] = []
        async for chunk in create_responses_sse_stream_from_chat_stream(upstream_stream(), tool_context):
            parts.append(chunk.decode())
        return "".join(parts)

    output = asyncio.run(collect())
    completed_output = _completed_response_output(output)

    assert [item["type"] for item in completed_output] == ["reasoning", "message", "function_call"]
    assert completed_output[2]["name"] == "shell"
    assert completed_output[2]["namespace"] == "codex"
    assert completed_output[2]["reasoning_content"] == "Need shell."


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


def test_stream_assistant_message_preserves_tool_calls_and_reasoning_for_replay() -> None:
    state = ResponsesStreamState(response_id="resp_stream_tools")

    state.push_reasoning_delta("Need tool first.")
    state.push_tool_call_delta(
        {
            "index": 0,
            "id": "call_weather",
            "function": {"name": "weather", "arguments": '{"city":"Seoul"}'},
        },
        "Need tool first.",
    )
    state.set_finish_reason("tool_calls")
    state.finalize()

    assistant = state.build_assistant_message()
    assert assistant is not None
    assert assistant.tool_calls is not None
    assert assistant.tool_calls[0]["id"] == "call_weather"
    assert assistant.tool_calls[0]["function"]["name"] == "weather"
    assert assistant.reasoning_content == "Need tool first."


def test_tool_state_finalize_custom_tool_preserves_reasoning_and_done_events() -> None:
    context = BridgeToolContext()
    context.add_custom_tool({"type": "custom", "name": "exec"})
    store = ToolStateStore(context)
    envelope = ResponseEnvelopeState(response_id="resp_custom_tool_finalize")

    store.push_delta(
        envelope,
        {
            "index": 0,
            "id": "call_exec",
            "function": {"name": "exec", "arguments": '{"input":"pwd"}'},
        },
        reasoning="Need shell access.",
    )
    events = b"".join(store.finalize(envelope)).decode()

    assert "event: response.custom_tool_call_input.done" in events
    assert "event: response.output_item.done" in events
    assert envelope.completed_output_items() == [
        {
            "id": "ctc_call_exec",
            "type": "custom_tool_call",
            "status": "completed",
            "call_id": "call_exec",
            "name": "exec",
            "input": "pwd",
            "reasoning_content": "Need shell access.",
        }
    ]


def test_tool_state_parallel_mixed_families_preserve_completed_output_order_by_index() -> None:
    context = BridgeToolContext()
    context.add_custom_tool({"type": "custom", "name": "exec"})
    context.add_tool_search_tool()
    store = ToolStateStore(context)
    envelope = ResponseEnvelopeState(response_id="resp_parallel_mixed_tools")

    chunks: list[bytes] = []
    chunks.extend(
        store.push_delta(
            envelope,
            {
                "index": 2,
                "id": "call_search",
                "function": {"name": "tool_search"},
            },
            reasoning="Need several tools.",
        )
    )
    chunks.extend(
        store.push_delta(
            envelope,
            {
                "index": 0,
                "id": "call_exec",
                "function": {"name": "exec"},
            },
            reasoning="Need several tools.",
        )
    )
    chunks.extend(
        store.push_delta(
            envelope,
            {
                "index": 1,
                "id": "call_fn",
                "function": {"name": "read_file", "arguments": '{"path":"/tmp/x"}'},
            },
            reasoning="Need several tools.",
        )
    )
    chunks.extend(
        store.push_delta(
            envelope,
            {
                "index": 2,
                "function": {"arguments": '{"query":"gmail"}'},
            },
            reasoning="Need several tools.",
        )
    )
    chunks.extend(
        store.push_delta(
            envelope,
            {
                "index": 0,
                "function": {"arguments": '{"input":"p'},
            },
            reasoning="Need several tools.",
        )
    )
    chunks.extend(
        store.push_delta(
            envelope,
            {
                "index": 0,
                "function": {"arguments": 'wd"}'},
            },
            reasoning="Need several tools.",
        )
    )
    chunks.extend(store.finalize(envelope))
    output = b"".join(chunks).decode()

    assert '"call_id": "call_search", "execution": "client", "arguments": {}, "reasoning_content": "Need several tools."' in output
    assert envelope.completed_output_items() == [
        {
            "id": "ctc_call_exec",
            "type": "custom_tool_call",
            "status": "completed",
            "call_id": "call_exec",
            "name": "exec",
            "input": "pwd",
            "reasoning_content": "Need several tools.",
        },
        {
            "id": "fc_call_fn",
            "type": "function_call",
            "status": "completed",
            "call_id": "call_fn",
            "name": "read_file",
            "arguments": '{"path":"/tmp/x"}',
            "reasoning_content": "Need several tools.",
        },
        {
            "id": "fc_call_search",
            "type": "tool_search_call",
            "status": "completed",
            "call_id": "call_search",
            "execution": "client",
            "arguments": {"query": "gmail"},
            "reasoning_content": "Need several tools.",
        },
    ]


def test_tool_state_interleaved_partial_argument_chunks_preserve_per_call_outputs() -> None:
    context = BridgeToolContext()
    context.add_tool_search_tool()
    store = ToolStateStore(context)
    envelope = ResponseEnvelopeState(response_id="resp_interleaved_partial_args")

    chunks: list[bytes] = []
    chunks.extend(
        store.push_delta(
            envelope,
            {
                "index": 0,
                "id": "call_fn",
                "function": {"name": "read_file"},
            },
            reasoning="Need file and search.",
        )
    )
    chunks.extend(
        store.push_delta(
            envelope,
            {
                "index": 1,
                "id": "call_search",
                "function": {"name": "tool_search"},
            },
            reasoning="Need file and search.",
        )
    )
    chunks.extend(
        store.push_delta(
            envelope,
            {
                "index": 0,
                "function": {"arguments": '{"path":"/tmp/'},
            },
            reasoning="Need file and search.",
        )
    )
    chunks.extend(
        store.push_delta(
            envelope,
            {
                "index": 1,
                "function": {"arguments": '{"query":"gm'},
            },
            reasoning="Need file and search.",
        )
    )
    chunks.extend(
        store.push_delta(
            envelope,
            {
                "index": 0,
                "function": {"arguments": 'x"}'},
            },
            reasoning="Need file and search.",
        )
    )
    chunks.extend(
        store.push_delta(
            envelope,
            {
                "index": 1,
                "function": {"arguments": 'ail"}'},
            },
            reasoning="Need file and search.",
        )
    )
    chunks.extend(store.finalize(envelope))
    output = b"".join(chunks).decode()

    assert output.count('"item_id": "fc_call_fn"') >= 3
    assert output.count('"item_id": "fc_call_search"') >= 3
    assert envelope.completed_output_items() == [
        {
            "id": "fc_call_fn",
            "type": "function_call",
            "status": "completed",
            "call_id": "call_fn",
            "name": "read_file",
            "arguments": '{"path":"/tmp/x"}',
            "reasoning_content": "Need file and search.",
        },
        {
            "id": "fc_call_search",
            "type": "tool_search_call",
            "status": "completed",
            "call_id": "call_search",
            "execution": "client",
            "arguments": {"query": "gmail"},
            "reasoning_content": "Need file and search.",
        },
    ]


def test_tool_state_custom_input_delta_preserves_chunk_granularity() -> None:
    context = BridgeToolContext()
    context.add_custom_tool({"type": "custom", "name": "exec"})
    store = ToolStateStore(context)
    envelope = ResponseEnvelopeState(response_id="resp_custom_input_chunks")

    chunks: list[bytes] = []
    chunks.extend(
        store.push_delta(
            envelope,
            {
                "index": 0,
                "id": "call_exec",
                "function": {"name": "exec"},
            },
            reasoning="Need shell.",
        )
    )
    chunks.extend(
        store.push_delta(
            envelope,
            {
                "index": 0,
                "function": {"arguments": '{"input":"p'},
            },
            reasoning="Need shell.",
        )
    )
    chunks.extend(
        store.push_delta(
            envelope,
            {
                "index": 0,
                "function": {"arguments": 'w'},
            },
            reasoning="Need shell.",
        )
    )
    chunks.extend(
        store.push_delta(
            envelope,
            {
                "index": 0,
                "function": {"arguments": 'd"}'},
            },
            reasoning="Need shell.",
        )
    )
    chunks.extend(store.finalize(envelope))
    output = b"".join(chunks).decode()

    assert output.count("event: response.custom_tool_call_input.delta") == 3
    assert '"delta": "p"' in output
    assert '"delta": "w"' in output
    assert '"delta": "d"' in output
    assert output.count("event: response.custom_tool_call_input.done") == 1
    assert envelope.completed_output_items() == [
        {
            "id": "ctc_call_exec",
            "type": "custom_tool_call",
            "status": "completed",
            "call_id": "call_exec",
            "name": "exec",
            "input": "pwd",
            "reasoning_content": "Need shell.",
        }
    ]


def test_tool_state_custom_input_delta_waits_for_split_unicode_escape_completion() -> None:
    context = BridgeToolContext()
    context.add_custom_tool({"type": "custom", "name": "exec"})
    store = ToolStateStore(context)
    envelope = ResponseEnvelopeState(response_id="resp_custom_unicode_chunks")

    chunks: list[bytes] = []
    chunks.extend(
        store.push_delta(
            envelope,
            {
                "index": 0,
                "id": "call_exec",
                "function": {"name": "exec"},
            },
            reasoning="Need unicode.",
        )
    )
    chunks.extend(
        store.push_delta(
            envelope,
            {
                "index": 0,
                "function": {"arguments": '{"input":"\\u4f'},
            },
            reasoning="Need unicode.",
        )
    )
    chunks.extend(
        store.push_delta(
            envelope,
            {
                "index": 0,
                "function": {"arguments": '60"}'},
            },
            reasoning="Need unicode.",
        )
    )
    chunks.extend(store.finalize(envelope))
    output = b"".join(chunks).decode()

    assert output.count("event: response.custom_tool_call_input.delta") == 1
    assert '"delta": "你"' in output
    assert '"input": "你"' in output
    assert envelope.completed_output_items()[0]["input"] == "你"


def test_tool_state_custom_input_delta_decodes_split_escaped_quote_without_raw_backslash_leak() -> None:
    context = BridgeToolContext()
    context.add_custom_tool({"type": "custom", "name": "exec"})
    store = ToolStateStore(context)
    envelope = ResponseEnvelopeState(response_id="resp_custom_quote_chunks")

    chunks: list[bytes] = []
    chunks.extend(
        store.push_delta(
            envelope,
            {
                "index": 0,
                "id": "call_exec",
                "function": {"name": "exec"},
            },
            reasoning="Need quote.",
        )
    )
    chunks.extend(
        store.push_delta(
            envelope,
            {
                "index": 0,
                "function": {"arguments": '{"input":"a\\'},
            },
            reasoning="Need quote.",
        )
    )
    chunks.extend(
        store.push_delta(
            envelope,
            {
                "index": 0,
                "function": {"arguments": '"b"}'},
            },
            reasoning="Need quote.",
        )
    )
    chunks.extend(store.finalize(envelope))
    output = b"".join(chunks).decode()

    assert '\\\\"b' not in output
    assert '"input": "a\\"b"' in output
    assert envelope.completed_output_items()[0]["input"] == 'a"b'


def test_stream_fail_preserves_parallel_mixed_tool_output_order() -> None:
    context = BridgeToolContext()
    context.add_custom_tool({"type": "custom", "name": "exec"})
    context.add_tool_search_tool()
    state = ResponsesStreamState(context, response_id="resp_parallel_fail")

    state.push_tool_call_delta({"index": 2, "id": "call_search", "function": {"name": "tool_search"}}, "Need tools.")
    state.push_tool_call_delta({"index": 0, "id": "call_exec", "function": {"name": "exec"}}, "Need tools.")
    state.push_tool_call_delta(
        {"index": 1, "id": "call_fn", "function": {"name": "read_file", "arguments": '{"path":"/tmp/x"}'}},
        "Need tools.",
    )
    state.push_tool_call_delta({"index": 2, "function": {"arguments": '{"query":"gmail"}'}}, "Need tools.")
    state.push_tool_call_delta({"index": 0, "function": {"arguments": '{"input":"pwd"}'}}, "Need tools.")

    output = b"".join(state.fail("bad request", "invalid_request_error")).decode()
    response = _response_payload_for_event(output, "response.failed")

    assert [item["call_id"] for item in response["output"]] == ["call_exec", "call_fn", "call_search"]
    assert response["error"]["message"] == "bad request"
    assert response["error"]["type"] == "invalid_request_error"


def test_stream_truncated_preserves_parallel_mixed_tool_output_order() -> None:
    context = BridgeToolContext()
    context.add_custom_tool({"type": "custom", "name": "exec"})
    context.add_tool_search_tool()
    state = ResponsesStreamState(context, response_id="resp_parallel_truncated")

    state.push_tool_call_delta({"index": 2, "id": "call_search", "function": {"name": "tool_search"}}, "Need tools.")
    state.push_tool_call_delta({"index": 0, "id": "call_exec", "function": {"name": "exec"}}, "Need tools.")
    state.push_tool_call_delta(
        {"index": 1, "id": "call_fn", "function": {"name": "read_file", "arguments": '{"path":"/tmp/x"}'}},
        "Need tools.",
    )
    state.push_tool_call_delta({"index": 2, "function": {"arguments": '{"query":"gmail"}'}}, "Need tools.")
    state.push_tool_call_delta({"index": 0, "function": {"arguments": '{"input":"pwd"}'}}, "Need tools.")

    output = b"".join(state.finalize()).decode()
    response = _response_payload_for_event(output, "response.completed")

    assert response["status"] == "incomplete"
    assert response["incomplete_details"] == {"reason": "stream_truncated"}
    assert [item["call_id"] for item in response["output"]] == ["call_exec", "call_fn", "call_search"]


def test_stream_fail_preserves_reasoning_message_and_parallel_tools_in_canonical_output_order() -> None:
    context = BridgeToolContext()
    context.add_custom_tool({"type": "custom", "name": "exec"})
    context.add_tool_search_tool()
    state = ResponsesStreamState(context, response_id="resp_mixed_lifecycle_fail")

    state.push_reasoning_delta("Need tools.")
    state.push_text_delta("hello")
    state.push_tool_call_delta({"index": 2, "id": "call_search", "function": {"name": "tool_search"}}, "Need tools.")
    state.push_tool_call_delta({"index": 0, "id": "call_exec", "function": {"name": "exec"}}, "Need tools.")
    state.push_tool_call_delta(
        {"index": 1, "id": "call_fn", "function": {"name": "read_file", "arguments": '{"path":"/tmp/x"}'}},
        "Need tools.",
    )
    state.push_tool_call_delta({"index": 2, "function": {"arguments": '{"query":"gmail"}'}}, "Need tools.")
    state.push_tool_call_delta({"index": 0, "function": {"arguments": '{"input":"pwd"}'}}, "Need tools.")

    output = b"".join(state.fail("bad request", "invalid_request_error")).decode()
    response = _response_payload_for_event(output, "response.failed")

    assert [item["type"] for item in response["output"]] == ["reasoning", "message", "custom_tool_call", "function_call", "tool_search_call"]
    assert [item.get("call_id") for item in response["output"][2:]] == ["call_exec", "call_fn", "call_search"]
    assert output.index("event: response.output_item.done") < output.index("event: response.failed")


def test_stream_truncated_preserves_reasoning_message_and_parallel_tools_in_canonical_output_order() -> None:
    context = BridgeToolContext()
    context.add_custom_tool({"type": "custom", "name": "exec"})
    context.add_tool_search_tool()
    state = ResponsesStreamState(context, response_id="resp_mixed_lifecycle_truncated")

    state.push_reasoning_delta("Need tools.")
    state.push_text_delta("hello")
    state.push_tool_call_delta({"index": 2, "id": "call_search", "function": {"name": "tool_search"}}, "Need tools.")
    state.push_tool_call_delta({"index": 0, "id": "call_exec", "function": {"name": "exec"}}, "Need tools.")
    state.push_tool_call_delta(
        {"index": 1, "id": "call_fn", "function": {"name": "read_file", "arguments": '{"path":"/tmp/x"}'}},
        "Need tools.",
    )
    state.push_tool_call_delta({"index": 2, "function": {"arguments": '{"query":"gmail"}'}}, "Need tools.")
    state.push_tool_call_delta({"index": 0, "function": {"arguments": '{"input":"pwd"}'}}, "Need tools.")

    output = b"".join(state.finalize()).decode()
    response = _response_payload_for_event(output, "response.completed")

    assert response["status"] == "incomplete"
    assert response["incomplete_details"] == {"reason": "stream_truncated"}
    assert [item["type"] for item in response["output"]] == ["reasoning", "message", "custom_tool_call", "function_call", "tool_search_call"]
    assert [item.get("call_id") for item in response["output"][2:]] == ["call_exec", "call_fn", "call_search"]


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


def test_response_envelope_failed_event_preserves_request_echo_and_completed_items() -> None:
    envelope = ResponseEnvelopeState(response_id="resp_bridge_failed")
    envelope.set_request_echo({"instructions": "be terse", "metadata": {"trace": "failed"}})
    envelope.append_completed_item(1, {"id": "item_1", "type": "message"})

    output = envelope.failed_event("bad request", "invalid_request_error").decode()

    assert 'event: response.failed' in output
    assert '"instructions": "be terse"' in output
    assert '"metadata": {"trace": "failed"}' in output
    assert '"message": "bad request"' in output
    assert '"type": "invalid_request_error"' in output
    assert '"output": [{"id": "item_1", "type": "message"}]' in output


def test_response_envelope_ensure_started_emits_created_and_in_progress_once() -> None:
    envelope = ResponseEnvelopeState(response_id="resp_started_once")

    output = b"".join(envelope.ensure_started()).decode()

    assert "event: response.created" in output
    assert "event: response.in_progress" in output
    assert envelope.ensure_started() == []


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
