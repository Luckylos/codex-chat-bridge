from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

import httpx

from codex_chat_bridge.api.routes import app
from codex_chat_bridge.config import Settings


def _single_upstream_settings() -> Settings:
    return Settings(
        upstream_base_url="https://newapi.example.com/v1",
        upstream_api_key="test-key",
        upstream_timeout_seconds=30,
    )


async def _request(method: str, path: str, **kwargs):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, **kwargs)


def _request_sync(method: str, path: str, **kwargs):
    return asyncio.run(_request(method, path, **kwargs))


class RequestValidationSemanticsTests(unittest.TestCase):
    def test_chat_format_input_item_user_message(self) -> None:
        """input_items with role='user' via message item type."""
        captured_messages = []

        class FakeUpstreamClient:
            def __init__(self, settings) -> None:
                self.settings = settings

            async def create_chat_completion(self, payload):
                captured_messages.append([message.model_dump(exclude_none=True) for message in payload.messages])
                return {
                    "id": "chatcmpl_demo",
                    "object": "chat.completion",
                    "created": 1710000000,
                    "model": payload.model,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }

        with patch("codex_chat_bridge.api.response_service.get_settings", return_value=_single_upstream_settings()), patch(
            "codex_chat_bridge.api.response_service.UpstreamClient", FakeUpstreamClient,
        ):
            response = _request_sync("POST", "/v1/responses", json={
                "model": "test-model",
                "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello from chat item"}]}],
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(captured_messages), 1)
        self.assertEqual(captured_messages[0][0]["content"], "hello from chat item")
        self.assertEqual(captured_messages[0][0]["role"], "user")

    def test_chat_format_input_item_assistant_with_reasoning_and_tool_calls(self) -> None:
        """input_items with role='assistant' containing tool_calls and reasoning_content."""
        captured_messages = []

        class FakeUpstreamClient:
            def __init__(self, settings) -> None:
                self.settings = settings

            async def create_chat_completion(self, payload):
                captured_messages.append([message.model_dump(exclude_none=True) for message in payload.messages])
                return {
                    "id": "chatcmpl_demo",
                    "object": "chat.completion",
                    "created": 1710000000,
                    "model": payload.model,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }

        with patch("codex_chat_bridge.api.response_service.get_settings", return_value=_single_upstream_settings()), patch(
            "codex_chat_bridge.api.response_service.UpstreamClient", FakeUpstreamClient,
        ):
            response = _request_sync("POST", "/v1/responses", json={
                "model": "test-model",
                "input": [
                    {"role": "user", "content": "trigger"},
                    {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "Need data first.",
                        "tool_calls": [
                            {"id": "call_x1", "type": "function", "function": {"name": "get_data", "arguments": "{}"}},
                        ],
                    },
                    {"role": "tool", "tool_call_id": "call_x1", "content": "data ok"},
                    {"role": "user", "content": "summarize"},
                ],
            })

        self.assertEqual(response.status_code, 200)
        msgs = captured_messages[0]
        self.assertEqual(len(msgs), 4)
        # assistant message preserves reasoning and tool_calls
        self.assertEqual(msgs[1]["role"], "assistant")
        self.assertEqual(msgs[1].get("reasoning_content"), "Need data first.")
        self.assertEqual(len(msgs[1]["tool_calls"]), 1)
        self.assertEqual(msgs[2]["role"], "tool")
        self.assertEqual(msgs[2]["tool_call_id"], "call_x1")

    def test_chat_format_input_item_system_and_developer_mapped_to_system(self) -> None:
        """input_items with role='system' or 'developer' map to chat system role."""
        captured_messages = []

        class FakeUpstreamClient:
            def __init__(self, settings) -> None:
                self.settings = settings

            async def create_chat_completion(self, payload):
                captured_messages.append([message.model_dump(exclude_none=True) for message in payload.messages])
                return {
                    "id": "chatcmpl_demo",
                    "object": "chat.completion",
                    "created": 1710000000,
                    "model": payload.model,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }

        with patch("codex_chat_bridge.api.response_service.get_settings", return_value=_single_upstream_settings()), patch(
            "codex_chat_bridge.api.response_service.UpstreamClient", FakeUpstreamClient,
        ):
            response = _request_sync("POST", "/v1/responses", json={
                "model": "test-model",
                "input": [
                    {"role": "developer", "content": [{"type": "input_text", "text": "dev note"}]},
                    {"role": "system", "content": "sys note"},
                    {"role": "user", "content": "hello"},
                ],
            })

        self.assertEqual(response.status_code, 200)
        msgs = captured_messages[0]
        # developer + system messages are collapsed to head and merged into one
        self.assertEqual(msgs[0]["role"], "system")
        self.assertIn("dev note", msgs[0]["content"])
        self.assertIn("sys note", msgs[0]["content"])
        self.assertEqual(msgs[1]["role"], "user")

    def test_chat_format_input_item_with_previous_response_id(self) -> None:
        """Chat format input_items work with previous_response_id session recovery."""
        captured_requests = []

        class FakeUpstream:
            def __init__(self, settings) -> None:
                self.call_count = 0

            async def create_chat_completion(self, payload):
                self.call_count += 1
                captured_requests.append([message.model_dump(exclude_none=True) for message in payload.messages])
                return {
                    "id": f"chatcmpl_{self.call_count}",
                    "object": "chat.completion",
                    "created": 123,
                    "model": "test-model",
                    "choices": [{"message": {"role": "assistant", "content": f"R{self.call_count}"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
                }

            async def list_models(self):
                return []

        fake = FakeUpstream(None)
        fake.settings = None

        with patch("codex_chat_bridge.api.response_service.get_settings", return_value=_single_upstream_settings()), patch(
            "codex_chat_bridge.api.response_service.UpstreamClient", return_value=fake,
        ):
            # First request
            resp_a = _request_sync("POST", "/v1/responses", json={
                "model": "test-model",
                "input": [{"role": "user", "content": "first"}],
            })
            self.assertEqual(resp_a.status_code, 200)
            rid = resp_a.json()["id"]

            # Second request — also uses chat format items
            resp_b = _request_sync("POST", "/v1/responses", json={
                "previous_response_id": rid,
                "input": [{"role": "user", "content": "second"}, {"role": "user", "content": "third"}],
            })
            self.assertEqual(resp_b.status_code, 200)
            self.assertEqual(fake.__dict__.get("call_count", 0), 2)

            # Upstream received: [prev_messages_context..., first, second, third]
            # Context from session = ["first", "R1"], + "second", "third"
            final_msgs = captured_requests[1]
            self.assertGreaterEqual(len(final_msgs), 3)
            # The session recovered messages include the previous assistant response
            self.assertTrue(any("R1" in str(m) for m in final_msgs))

    def test_chat_format_input_item_unknown_role_falls_back_to_user(self) -> None:
        """Unknown role in chat format input items falls back to user."""
        captured_messages = []

        class FakeUpstreamClient:
            def __init__(self, settings) -> None:
                self.settings = settings

            async def create_chat_completion(self, payload):
                captured_messages.append([message.model_dump(exclude_none=True) for message in payload.messages])
                return {
                    "id": "chatcmpl_demo",
                    "object": "chat.completion",
                    "created": 1710000000,
                    "model": payload.model,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }

        with patch("codex_chat_bridge.api.response_service.get_settings", return_value=_single_upstream_settings()), patch(
            "codex_chat_bridge.api.response_service.UpstreamClient", FakeUpstreamClient,
        ):
            response = _request_sync("POST", "/v1/responses", json={
                "model": "test-model",
                "input": [{"role": "unknown_bot", "content": "hello"}],
            })

        self.assertEqual(response.status_code, 200)
        msgs = captured_messages[0]
        self.assertEqual(msgs[0]["role"], "user")

    def test_models_endpoint_passthroughs_upstream_catalog(self) -> None:
        class FakeUpstreamClient:
            def __init__(self, settings) -> None:
                self.settings = settings

            async def list_models(self):
                return [
                    {"id": "deepseek-v4-flash-free", "object": "model", "owned_by": "openai"},
                    {"id": "deepseek-v4-flash", "object": "model", "owned_by": "openai"},
                ]

        with patch("codex_chat_bridge.api.routes.get_settings", return_value=_single_upstream_settings()), patch(
            "codex_chat_bridge.api.routes.UpstreamClient",
            FakeUpstreamClient,
        ):
            response = _request_sync("GET", "/v1/models")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["object"], "list")
        self.assertEqual(
            [model["id"] for model in body["data"]],
            ["deepseek-v4-flash-free", "deepseek-v4-flash"],
        )

    def test_unsupported_top_level_item_returns_local_400_before_upstream(self) -> None:
        class UpstreamShouldNotBeCalled:
            def __init__(self, settings) -> None:
                raise AssertionError("UpstreamClient should not be instantiated for empty effective input")

        with patch("codex_chat_bridge.api.response_service.get_settings", return_value=_single_upstream_settings()), patch(
            "codex_chat_bridge.api.response_service.UpstreamClient",
            UpstreamShouldNotBeCalled,
        ):
            response = _request_sync("POST", 
                "/v1/responses",
                json={
                    "model": "deepseek-v4-flash",
                    "input": [
                        {"type": "input_image", "image_url": "file:///etc/passwd"}  # unsafe URL → rejected
                    ]
                },
            )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["error"]["type"], "invalid_request_error")
        self.assertEqual(body["error"]["code"], "empty_effective_input")
        self.assertIn("No supported Responses input items remained after bridge normalization", body["error"]["message"])

    def test_blank_text_input_returns_local_400_before_upstream(self) -> None:
        class UpstreamShouldNotBeCalled:
            def __init__(self, settings) -> None:
                raise AssertionError("UpstreamClient should not be instantiated for blank effective input")

        with patch("codex_chat_bridge.api.response_service.get_settings", return_value=_single_upstream_settings()), patch(
            "codex_chat_bridge.api.response_service.UpstreamClient",
            UpstreamShouldNotBeCalled,
        ):
            response = _request_sync("POST", 
                "/v1/responses",
                json={"model": "deepseek-v4-flash", "input": "   \n  \t"},
            )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["error"]["type"], "invalid_request_error")
        self.assertEqual(body["error"]["code"], "blank_effective_input")
        self.assertIn("blank or semantically empty messages", body["error"]["message"])

    def test_missing_model_returns_local_400(self) -> None:
        class UpstreamShouldNotBeCalled:
            def __init__(self, settings) -> None:
                raise AssertionError("UpstreamClient should not be instantiated when model is missing")

        with patch("codex_chat_bridge.api.response_service.get_settings", return_value=_single_upstream_settings()), patch(
            "codex_chat_bridge.api.response_service.UpstreamClient",
            UpstreamShouldNotBeCalled,
        ):
            response = _request_sync("POST", "/v1/responses", json={"input": "ping"})

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["error"]["code"], "missing_model")
        self.assertIn("missing required field: model", body["error"]["message"])

    def test_mixed_supported_and_unsupported_input_still_reaches_upstream(self) -> None:
        captured_messages = []

        class FakeUpstreamClient:
            def __init__(self, settings) -> None:
                self.settings = settings

            async def create_chat_completion(self, payload):
                captured_messages.append([message.model_dump(exclude_none=True) for message in payload.messages])
                return {
                    "id": "chatcmpl_demo",
                    "object": "chat.completion",
                    "created": 1710000000,
                    "model": payload.model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }

        with patch("codex_chat_bridge.api.response_service.get_settings", return_value=_single_upstream_settings()), patch(
            "codex_chat_bridge.api.response_service.UpstreamClient",
            FakeUpstreamClient,
        ):
            response = _request_sync("POST", 
                "/v1/responses",
                json={
                    "model": "deepseek-v4-flash",
                    "input": [
                        {"role": "user", "content": [
                            {"type": "input_text", "text": "ping"},
                            {"type": "input_audio", "audio_url": "https://example.com/a.mp3"}
                        ]}
                    ]
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["output_text"], "ok")
        # input_audio is now supported, so content is a structured list
        self.assertEqual(len(captured_messages[0]), 1)
        self.assertEqual(captured_messages[0][0]["role"], "user")
        self.assertIsInstance(captured_messages[0][0]["content"], list)

    def test_instructions_only_context_is_still_allowed_to_reach_upstream(self) -> None:
        captured_messages = []

        class FakeUpstreamClient:
            def __init__(self, settings) -> None:
                self.settings = settings

            async def create_chat_completion(self, payload):
                captured_messages.append([message.model_dump(exclude_none=True) for message in payload.messages])
                return {
                    "id": "chatcmpl_demo",
                    "object": "chat.completion",
                    "created": 1710000000,
                    "model": payload.model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }

        with patch("codex_chat_bridge.api.response_service.get_settings", return_value=_single_upstream_settings()), patch(
            "codex_chat_bridge.api.response_service.UpstreamClient",
            FakeUpstreamClient,
        ):
            response = _request_sync("POST", 
                "/v1/responses",
                json={"model": "deepseek-v4-flash", "instructions": "be helpful", "input": "   \n  \t"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["output_text"], "ok")
        self.assertEqual(
            captured_messages[0],
            [{"role": "system", "content": "be helpful"}, {"role": "user", "content": "   \n  \t"}],
        )


if __name__ == "__main__":
    unittest.main()


class PreviousResponseIdIntegrationTests(unittest.TestCase):
    """Non-streaming previous_response_id roundtrip via HTTP API."""

    def test_previous_response_id_roundtrip(self) -> None:
        class FakeUpstream:
            def __init__(self, settings) -> None:
                self.call_count = 0

            async def create_chat_completion(self, payload):
                self.call_count += 1
                return {
                    "id": f"chatcmpl_{self.call_count}",
                    "object": "chat.completion",
                    "created": 123,
                    "model": "test-model",
                    "choices": [{
                        "message": {"role": "assistant", "content": f"Response {self.call_count}"},
                        "finish_reason": "stop",
                    }],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
                }

            async def list_models(self):
                return []

        fake = FakeUpstream(None)
        fake.settings = None

        with patch("codex_chat_bridge.api.response_service.get_settings", return_value=_single_upstream_settings()), patch(
            "codex_chat_bridge.api.response_service.UpstreamClient", return_value=fake,
        ):
            # Request A
            resp_a = _request_sync("POST", "/v1/responses", json={
                "model": "test-model",
                "input": "First message",
            })
            self.assertEqual(resp_a.status_code, 200)
            body_a = resp_a.json()
            rid = body_a["id"]
            self.assertTrue(rid.startswith("resp_bridge_"), f"Expected bridge id, got {rid}")
            self.assertEqual(fake.call_count, 1)

            # Request B with previous_response_id
            resp_b = _request_sync("POST", "/v1/responses", json={
                "previous_response_id": rid,
                "input": "Second message",
            })
            self.assertEqual(resp_b.status_code, 200)

            # Session was saved: upstream received context + new input
            self.assertEqual(fake.call_count, 2)
