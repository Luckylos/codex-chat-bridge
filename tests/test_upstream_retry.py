from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

import httpx

from codex_chat_bridge.config import Settings
from codex_chat_bridge.models import ChatCompletionsRequest
from codex_chat_bridge.upstream import UpstreamClient, _backoff_delay, _retryable_status


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        *,
        text: str = "",
        json_body: dict | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status_code = status_code
        self._text = text
        self._json_body = json_body
        self._chunks = chunks or []
        self.request = httpx.Request("POST", "https://newapi.example.com/v1/chat/completions")

    @property
    def text(self) -> str:
        return self._text

    async def aread(self) -> bytes:
        return self._text.encode()

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = httpx.Response(self.status_code, request=self.request)
            raise httpx.HTTPStatusError(self._text or f"HTTP {self.status_code}", request=self.request, response=response)

    def json(self) -> dict:
        if self._json_body is None:
            raise AssertionError("json() requested without json_body")
        return self._json_body


class FakeAsyncClient:
    response_queue: list[FakeResponse] = []
    captured_requests: list[dict] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    def build_request(self, method: str, url: str, headers=None, json=None):
        return type("FakeRequest", (), {"method": method, "url": url, "headers": headers, "json": json})()

    async def post(self, url: str, headers=None, json=None):
        self.captured_requests.append(dict(json or {}))
        return self._next_response()

    async def send(self, request, stream: bool = False):
        self.captured_requests.append(dict(getattr(request, "json", {}) or {}))
        return self._next_response()

    async def aclose(self) -> None:
        return None

    @classmethod
    def reset(cls) -> None:
        cls.response_queue = []
        cls.captured_requests = []

    @classmethod
    def _next_response(cls) -> FakeResponse:
        if not cls.response_queue:
            raise AssertionError("FakeAsyncClient.response_queue is empty")
        return cls.response_queue.pop(0)


class RetryLogicTests(unittest.TestCase):
    def test_retryable_status_429(self) -> None:
        self.assertTrue(_retryable_status(429))

    def test_retryable_status_500(self) -> None:
        self.assertTrue(_retryable_status(500))

    def test_retryable_status_503(self) -> None:
        self.assertTrue(_retryable_status(503))

    def test_retryable_status_200_is_not_retryable(self) -> None:
        self.assertFalse(_retryable_status(200))

    def test_retryable_status_400_is_not_retryable(self) -> None:
        self.assertFalse(_retryable_status(400))

    def test_backoff_delay_increases(self) -> None:
        d0 = _backoff_delay(0, base=0.5)
        d1 = _backoff_delay(1, base=0.5)
        d2 = _backoff_delay(2, base=0.5)
        self.assertLess(d0, d1)
        self.assertLess(d1, d2)

    def test_backoff_delay_capped(self) -> None:
        d = _backoff_delay(10, base=1, max_delay=30)
        self.assertAlmostEqual(d, 30.0, delta=1.5)

    def test_backoff_delay_has_jitter(self) -> None:
        delays = {_backoff_delay(0, base=1, max_delay=10) for _ in range(20)}
        self.assertGreater(len(delays), 1)


class UpstreamCompatRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeAsyncClient.reset()
        self.client = UpstreamClient(Settings(
            upstream_base_url="https://newapi.example.com/v1",
            upstream_api_key="test-key",
            upstream_timeout_seconds=30,
            upstream_max_retries=0,
        ))

    def _payload(self, model: str, effort: str | None = "high") -> ChatCompletionsRequest:
        body: dict[str, object] = {
            "model": model,
            "messages": [{"role": "user", "content": "hello"}],
        }
        if effort is not None:
            body["reasoning_effort"] = effort
        return ChatCompletionsRequest.model_validate(body)

    def test_non_streaming_openai_like_effort_rejection_falls_back_to_provider_default(self) -> None:
        FakeAsyncClient.response_queue = [
            FakeResponse(400, text="Unsupported parameter(s): reasoning_effort"),
            FakeResponse(200, json_body={"id": "ok", "object": "chat.completion"}),
        ]
        with patch("codex_chat_bridge.upstream.httpx.AsyncClient", FakeAsyncClient):
            result = asyncio.run(self.client.create_chat_completion(self._payload("gpt-5", "high")))

        self.assertEqual(result["id"], "ok")
        self.assertEqual(FakeAsyncClient.captured_requests[0]["reasoning_effort"], "high")
        self.assertNotIn("thinking", FakeAsyncClient.captured_requests[0])
        self.assertNotIn("thinking", FakeAsyncClient.captured_requests[1])
        self.assertNotIn("reasoning_effort", FakeAsyncClient.captured_requests[1])

    def test_non_streaming_glm_effort_rejection_falls_back_to_provider_default(self) -> None:
        # GLM now uses effort_only — same path as openai_like/deepseek.
        FakeAsyncClient.response_queue = [
            FakeResponse(400, text="Unsupported parameter(s): reasoning_effort"),
            FakeResponse(200, json_body={"id": "ok", "object": "chat.completion"}),
        ]
        with patch("codex_chat_bridge.upstream.httpx.AsyncClient", FakeAsyncClient):
            result = asyncio.run(self.client.create_chat_completion(self._payload("glm-5.2", "high")))

        self.assertEqual(result["id"], "ok")
        self.assertEqual(FakeAsyncClient.captured_requests[0]["reasoning_effort"], "high")
        self.assertNotIn("thinking", FakeAsyncClient.captured_requests[0])
        self.assertNotIn("thinking", FakeAsyncClient.captured_requests[1])
        self.assertNotIn("reasoning_effort", FakeAsyncClient.captured_requests[1])

    def test_non_streaming_glm_no_thinking_in_initial_request(self) -> None:
        # GLM never sends thinking — only reasoning_effort.
        FakeAsyncClient.response_queue = [
            FakeResponse(200, json_body={"id": "ok", "object": "chat.completion"}),
        ]
        with patch("codex_chat_bridge.upstream.httpx.AsyncClient", FakeAsyncClient):
            result = asyncio.run(self.client.create_chat_completion(self._payload("glm-5.2", "high")))

        self.assertEqual(result["id"], "ok")
        self.assertEqual(FakeAsyncClient.captured_requests[0]["reasoning_effort"], "high")
        self.assertNotIn("thinking", FakeAsyncClient.captured_requests[0])

    def test_streaming_openai_like_effort_rejection_falls_back_to_provider_default(self) -> None:
        FakeAsyncClient.response_queue = [
            FakeResponse(400, text="Unsupported parameter(s): reasoning_effort"),
            FakeResponse(200, chunks=[b"chunk-1", b"chunk-2"]),
        ]

        async def collect() -> bytes:
            parts: list[bytes] = []
            async for chunk in self.client.stream_chat_completion(self._payload("deepseek-v4-flash", "high")):
                parts.append(chunk)
            return b"".join(parts)

        with patch("codex_chat_bridge.upstream.httpx.AsyncClient", FakeAsyncClient):
            body = asyncio.run(collect())

        self.assertEqual(body, b"chunk-1chunk-2")
        self.assertEqual(FakeAsyncClient.captured_requests[0]["reasoning_effort"], "high")
        self.assertNotIn("thinking", FakeAsyncClient.captured_requests[0])
        self.assertNotIn("thinking", FakeAsyncClient.captured_requests[1])
        self.assertNotIn("reasoning_effort", FakeAsyncClient.captured_requests[1])

    def test_streaming_glm_none_uses_effort_only(self) -> None:
        # GLM with effort=none now sends reasoning_effort=none, not thinking.
        FakeAsyncClient.response_queue = [
            FakeResponse(200, chunks=[b"ok"]),
        ]

        async def collect() -> bytes:
            parts: list[bytes] = []
            async for chunk in self.client.stream_chat_completion(self._payload("glm-5.2", "none")):
                parts.append(chunk)
            return b"".join(parts)

        with patch("codex_chat_bridge.upstream.httpx.AsyncClient", FakeAsyncClient):
            body = asyncio.run(collect())

        self.assertEqual(body, b"ok")
        self.assertEqual(FakeAsyncClient.captured_requests[0]["reasoning_effort"], "none")
        self.assertNotIn("thinking", FakeAsyncClient.captured_requests[0])

    def test_streaming_keeps_non_reasoning_compat_rules(self) -> None:
        FakeAsyncClient.response_queue = [
            FakeResponse(400, text="Unsupported parameter(s): parallel_tool_calls"),
            FakeResponse(200, chunks=[b"ok"]),
        ]
        payload = ChatCompletionsRequest.model_validate({
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "hello"}],
            "reasoning_effort": "high",
            "parallel_tool_calls": True,
        })

        async def collect() -> bytes:
            parts: list[bytes] = []
            async for chunk in self.client.stream_chat_completion(payload):
                parts.append(chunk)
            return b"".join(parts)

        with patch("codex_chat_bridge.upstream.httpx.AsyncClient", FakeAsyncClient):
            body = asyncio.run(collect())

        self.assertEqual(body, b"ok")
        self.assertTrue(FakeAsyncClient.captured_requests[0]["parallel_tool_calls"])
        # GLM uses effort_only — reasoning_effort without thinking
        self.assertEqual(FakeAsyncClient.captured_requests[0]["reasoning_effort"], "high")
        self.assertNotIn("thinking", FakeAsyncClient.captured_requests[0])
        self.assertNotIn("parallel_tool_calls", FakeAsyncClient.captured_requests[1])
        self.assertEqual(FakeAsyncClient.captured_requests[1]["reasoning_effort"], "high")
        self.assertNotIn("thinking", FakeAsyncClient.captured_requests[1])
