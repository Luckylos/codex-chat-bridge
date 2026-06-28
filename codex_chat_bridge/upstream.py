from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import logging
from typing import Any

import httpx

from .config import Settings
from .reasoning_policy import ReasoningRequestState, build_initial_reasoning_state
from .upstream_compat import UpstreamCompatPolicy
from .upstream_transport import (
    backoff_delay,
    close_response_client,
    read_error_text,
    retryable_exception,
    retryable_status,
    send_once,
)

_logger = logging.getLogger("codex-chat-bridge.upstream")

# Keep stable module-level names for tests and existing callers.
_retryable_status = retryable_status
_backoff_delay = backoff_delay


class UpstreamClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._compat_policy = UpstreamCompatPolicy()

    def _chat_completions_url(self) -> str:
        if not self._settings.upstream_base_url:
            raise RuntimeError("BRIDGE_UPSTREAM_BASE_URL is empty")

        base = self._settings.upstream_base_url.rstrip("/")
        if base.endswith("/v1/chat/completions"):
            return base
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    def _models_url(self) -> str:
        if not self._settings.upstream_base_url:
            raise RuntimeError("BRIDGE_UPSTREAM_BASE_URL is empty")

        base = self._settings.upstream_base_url.rstrip("/")
        if base.endswith("/v1/models"):
            return base
        if base.endswith("/models"):
            return base
        if base.endswith("/v1"):
            return f"{base}/models"
        return f"{base}/v1/models"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._settings.upstream_api_key:
            headers["Authorization"] = f"Bearer {self._settings.upstream_api_key}"
        return headers

    async def _send_with_compat_retry(
        self,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        *,
        is_stream: bool,
    ) -> tuple[httpx.Response, httpx.AsyncClient | None, ReasoningRequestState]:
        current_state = build_initial_reasoning_state(body)

        while True:
            response, client = await send_once(
                httpx.AsyncClient,
                self._settings.upstream_timeout_seconds,
                url,
                headers,
                current_state.body,
                is_stream=is_stream,
            )
            if response.status_code != 400:
                return response, client, current_state

            error_text = await read_error_text(response)
            compat_retry = self._compat_policy.retry_state(current_state, error_text)
            await close_response_client(response, client)
            if compat_retry is None:
                _logger.warning(
                    "upstream 400 with no compatible retry: stream=%s model=%s %.200s",
                    is_stream,
                    current_state.body.get("model"),
                    error_text,
                )
                return response, None, current_state

            compat_label, next_state = compat_retry
            _logger.info(
                "upstream 400 retry: stream=%s model=%s compat=%s bucket=%s from=%s to=%s effort=%s",
                is_stream,
                current_state.body.get("model"),
                compat_label,
                current_state.bucket,
                current_state.wire_mode,
                next_state.wire_mode,
                current_state.canonical_effort,
            )
            current_state = next_state

    async def _request_with_retry(
        self,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        *,
        is_stream: bool,
    ) -> tuple[httpx.Response, httpx.AsyncClient | None]:
        max_retries = self._settings.upstream_max_retries
        current_body = dict(body)

        for attempt in range(max_retries + 1):
            try:
                response, client, request_state = await self._send_with_compat_retry(
                    url,
                    headers,
                    current_body,
                    is_stream=is_stream,
                )
                current_body = request_state.body

                if retryable_status(response.status_code) and attempt < max_retries:
                    await close_response_client(response, client)
                    delay = backoff_delay(attempt)
                    _logger.warning(
                        "upstream %d %s (attempt %d/%d) — retry in %.1fs",
                        response.status_code,
                        "stream" if is_stream else "body",
                        attempt + 1,
                        max_retries + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                response.raise_for_status()
                return response, client

            except Exception as exc:
                if retryable_exception(exc) and attempt < max_retries:
                    delay = backoff_delay(attempt)
                    _logger.warning(
                        "upstream %s network error %r (attempt %d/%d) — retry in %.1fs",
                        "stream" if is_stream else "body",
                        exc,
                        attempt + 1,
                        max_retries + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise

        raise RuntimeError("retry loop exhausted unexpectedly")

    async def create_chat_completion(self, payload: Any) -> dict[str, Any]:
        body = payload.model_dump(mode="json", exclude_none=True)
        response, _client = await self._request_with_retry(
            self._chat_completions_url(),
            self._headers(),
            body,
            is_stream=False,
        )
        return response.json()

    async def stream_chat_completion(self, payload: Any) -> AsyncIterator[bytes]:
        body = payload.model_dump(mode="json", exclude_none=True)
        response, client = await self._request_with_retry(
            self._chat_completions_url(),
            self._headers(),
            body,
            is_stream=True,
        )
        if client is None:
            raise RuntimeError("stream response missing client handle")

        try:
            async for chunk in response.aiter_bytes():
                if chunk:
                    yield chunk
        finally:
            await close_response_client(response, client)

    async def list_models(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self._settings.upstream_timeout_seconds) as client:
            response = await client.get(self._models_url(), headers=self._headers())
            response.raise_for_status()
            body = response.json()
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            raise RuntimeError("Upstream /v1/models did not return an OpenAI-style list")
        return [item for item in data if isinstance(item, dict)]
