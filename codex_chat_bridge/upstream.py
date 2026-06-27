from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import logging
import random
from typing import Any

import httpx

from .config import Settings

_logger = logging.getLogger("codex-chat-bridge.upstream")

_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


def _retryable_status(status_code: int) -> bool:
    return status_code in _RETRYABLE_STATUSES


def _retryable_exception(exc: Exception) -> bool:
    """Network-level errors that are safe to retry."""
    return isinstance(exc, (httpx.TimeoutException, httpx.ConnectError,
                            httpx.RemoteProtocolError, httpx.ReadError,
                            httpx.WriteError))


def _backoff_delay(attempt: int, base: float = 0.5, max_delay: float = 30.0) -> float:
    """Exponential backoff with jitter: base * 2^attempt + random(0, base)."""
    delay = min(base * (2 ** attempt), max_delay)
    jitter = random.uniform(0, base)
    return delay + jitter


class UpstreamClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

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

    # ------------------------------------------------------------------
    # 400 compatible retry
    # upstream 返回 400 时按规则逐条尝试兼容修正，每次重试仅应用第一个匹配规则。
    # 规则按优先级排序；每个规则用 error_body 做二次确认。
    # ------------------------------------------------------------------

    @staticmethod
    def _top_p_out_of_range(body: dict, error_body: str) -> bool:
        return body.get("top_p") is not None and body["top_p"] >= 1

    @staticmethod
    def _apply_top_p_clamp(body: dict) -> dict:
        return {**body, "top_p": 0.999}

    @staticmethod
    def _stream_options_rejected(body: dict, error_body: str) -> bool:
        return body.get("stream_options") is not None

    @staticmethod
    def _apply_strip_stream_options(body: dict) -> dict:
        return {**body, "stream_options": None}

    @staticmethod
    def _include_usage_rejected(body: dict, error_body: str) -> bool:
        opts = body.get("stream_options")
        return isinstance(opts, dict) and opts.get("include_usage") is True

    @staticmethod
    def _apply_disable_include_usage(body: dict) -> dict:
        return {**body, "stream_options": {"include_usage": False}}

    @staticmethod
    def _parallel_tool_calls_rejected(body: dict, error_body: str) -> bool:
        return body.get("parallel_tool_calls") is not None

    @staticmethod
    def _apply_strip_parallel_tool_calls(body: dict) -> dict:
        return {**body, "parallel_tool_calls": None}

    @staticmethod
    def _platform_thinking_not_supported(body: dict, error_body: str) -> bool:
        return body.get("thinking") is not None

    @staticmethod
    def _apply_strip_thinking(body: dict) -> dict:
        return {**body, "thinking": None}

    _RETRY_RULES: list[tuple] = [
        (_top_p_out_of_range, _apply_top_p_clamp),
        (_include_usage_rejected, _apply_disable_include_usage),
        (_stream_options_rejected, _apply_strip_stream_options),
        (_parallel_tool_calls_rejected, _apply_strip_parallel_tool_calls),
        (_platform_thinking_not_supported, _apply_strip_thinking),
    ]

    def _retry_body(self, body: dict, error_body: str) -> dict | None:
        """对 body 应用第一个匹配的兼容规则，返回修正后的 body 或 None（无匹配）。"""
        for check, apply in self._RETRY_RULES:
            if check(self, body, error_body):
                return apply(self, body)
        return None

    # ------------------------------------------------------------------
    # Transient retry (429 / 5xx / network errors)
    # ------------------------------------------------------------------

    async def _post_with_retry(
        self, url: str, headers: dict, body: dict,
        *, is_stream: bool = False,
    ) -> httpx.Response:
        """POST with retry on 429/5xx/network errors using exponential backoff."""
        max_retries = self._settings.upstream_max_retries
        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=self._settings.upstream_timeout_seconds,
                ) as client:
                    if is_stream:
                        response = await client.send(
                            client.build_request("POST", url, headers=headers, json=body),
                        )
                    else:
                        response = await client.post(url, headers=headers, json=body)

                if response.status_code == 400:
                    # Existing 400-compatible retry (one attempt)
                    error_text = response.text
                    retried = self._retry_body(body, error_text)
                    if retried is not None:
                        _logger.info(
                            "upstream 400 retry: mode=%s",
                            [p[0].__name__ for p in self._RETRY_RULES
                             if p[0](self, body, error_text)],
                        )
                        async with httpx.AsyncClient(
                            timeout=self._settings.upstream_timeout_seconds,
                        ) as client:
                            if is_stream:
                                response = await client.send(
                                    client.build_request("POST", url, headers=headers, json=retried),
                                )
                            else:
                                response = await client.post(url, headers=headers, json=retried)
                    else:
                        _logger.warning("upstream 400 with no compatible retry: %.200s", error_text)
                    response.raise_for_status()
                    return response

                if _retryable_status(response.status_code) and attempt < max_retries:
                    delay = _backoff_delay(attempt)
                    _logger.warning(
                        "upstream %d %s (attempt %d/%d) — retry in %.1fs",
                        response.status_code, "stream" if is_stream else "body",
                        attempt + 1, max_retries + 1, delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                response.raise_for_status()
                return response

            except (httpx.TimeoutException, httpx.ConnectError,
                    httpx.RemoteProtocolError, httpx.ReadError,
                    httpx.WriteError) as exc:
                last_error = exc
                if attempt < max_retries:
                    delay = _backoff_delay(attempt)
                    _logger.warning(
                        "upstream network error %r (attempt %d/%d) — retry in %.1fs",
                        exc, attempt + 1, max_retries + 1, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise
        # NB: bare raise after loop is unreachable — final attempt either returns
        # (via response.raise_for_status()) or raises inside the except handler.


    async def create_chat_completion(self, payload: Any) -> dict[str, Any]:
        body = payload.model_dump(mode="json", exclude_none=True)
        url = self._chat_completions_url()
        headers = self._headers()

        response = await self._post_with_retry(url, headers, body)
        return response.json()

    async def stream_chat_completion(self, payload: Any) -> AsyncIterator[bytes]:
        body = payload.model_dump(mode="json", exclude_none=True)
        url = self._chat_completions_url()
        headers = self._headers()

        max_retries = self._settings.upstream_max_retries
        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=self._settings.upstream_timeout_seconds,
                ) as client:
                    async with client.stream(
                        "POST", url, headers=headers, json=body,
                    ) as response:
                        if _retryable_status(response.status_code) and attempt < max_retries:
                            delay = _backoff_delay(attempt)
                            _logger.warning(
                                "upstream %d stream (attempt %d/%d) — retry in %.1fs",
                                response.status_code, attempt + 1, max_retries + 1, delay,
                            )
                            await asyncio.sleep(delay)
                            continue
                        response.raise_for_status()
                        async for chunk in response.aiter_bytes():
                            if chunk:
                                yield chunk
                        return
            except (httpx.TimeoutException, httpx.ConnectError,
                    httpx.RemoteProtocolError, httpx.ReadError,
                    httpx.WriteError) as exc:
                if attempt < max_retries:
                    delay = _backoff_delay(attempt)
                    _logger.warning(
                        "upstream stream network error %r (attempt %d/%d) — retry in %.1fs",
                        exc, attempt + 1, max_retries + 1, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise

    async def list_models(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self._settings.upstream_timeout_seconds) as client:
            response = await client.get(self._models_url(), headers=self._headers())
            response.raise_for_status()
            body = response.json()
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            raise RuntimeError("Upstream /v1/models did not return an OpenAI-style list")
        return [item for item in data if isinstance(item, dict)]