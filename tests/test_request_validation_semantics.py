from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from codex_chat_bridge.api.routes import app
from codex_chat_bridge.config import Settings


def _single_upstream_settings() -> Settings:
    return Settings(
        upstream_base_url="https://newapi.example.com/v1",
        upstream_api_key="test-key",
        upstream_timeout_seconds=30,
        public_base_url="http://127.0.0.1:18090/v1",
    )


class RequestValidationSemanticsTests(unittest.TestCase):
    def test_models_endpoint_passthroughs_upstream_catalog(self) -> None:
        class FakeUpstreamClient:
            def __init__(self, settings) -> None:
                self.settings = settings

            async def list_models(self):
                return [
                    {"id": "deepseek-v4-flash-free", "object": "model", "owned_by": "openai"},
                    {"id": "deepseek-v4-flash", "object": "model", "owned_by": "openai"},
                ]

        client = TestClient(app)
        with patch("codex_chat_bridge.api.routes.get_settings", return_value=_single_upstream_settings()), patch(
            "codex_chat_bridge.api.routes.UpstreamClient",
            FakeUpstreamClient,
        ):
            response = client.get("/v1/models")

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

        client = TestClient(app)
        with patch("codex_chat_bridge.api.routes.get_settings", return_value=_single_upstream_settings()), patch(
            "codex_chat_bridge.api.routes.UpstreamClient",
            UpstreamShouldNotBeCalled,
        ):
            response = client.post(
                "/v1/responses",
                json={
                    "model": "deepseek-v4-flash",
                    "input": [
                        {"type": "input_audio", "audio_url": "https://example.com/a.mp3"}
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

        client = TestClient(app)
        with patch("codex_chat_bridge.api.routes.get_settings", return_value=_single_upstream_settings()), patch(
            "codex_chat_bridge.api.routes.UpstreamClient",
            UpstreamShouldNotBeCalled,
        ):
            response = client.post(
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

        client = TestClient(app)
        with patch("codex_chat_bridge.api.routes.get_settings", return_value=_single_upstream_settings()), patch(
            "codex_chat_bridge.api.routes.UpstreamClient",
            UpstreamShouldNotBeCalled,
        ):
            response = client.post("/v1/responses", json={"input": "ping"})

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

        client = TestClient(app)
        with patch("codex_chat_bridge.api.routes.get_settings", return_value=_single_upstream_settings()), patch(
            "codex_chat_bridge.api.routes.UpstreamClient",
            FakeUpstreamClient,
        ):
            response = client.post(
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
        self.assertEqual(
            captured_messages[0],
            [{"role": "user", "content": "ping"}],
        )

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

        client = TestClient(app)
        with patch("codex_chat_bridge.api.routes.get_settings", return_value=_single_upstream_settings()), patch(
            "codex_chat_bridge.api.routes.UpstreamClient",
            FakeUpstreamClient,
        ):
            response = client.post(
                "/v1/responses",
                json={"model": "deepseek-v4-flash", "instructions": "be helpful", "input": "   \n  \t"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["output_text"], "ok")
        self.assertEqual(
            captured_messages[0],
            [{"role": "system", "content": "be helpful"}, {"role": "user", "content": ""}],
        )


if __name__ == "__main__":
    unittest.main()
