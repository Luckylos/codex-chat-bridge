from __future__ import annotations

from collections.abc import AsyncIterator
import logging
from typing import Any

import httpx

from .config import Settings

_logger = logging.getLogger("codex-chat-bridge.upstream")


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

    async def create_chat_completion(self, payload: Any) -> dict[str, Any]:
        body = payload.model_dump(mode="json", exclude_none=True)
        url = self._chat_completions_url()
        headers = self._headers()
        timeout = self._settings.upstream_timeout_seconds

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=body)
            if response.status_code == 400:
                error_text = response.text
                retried = self._retry_body(body, error_text)
                if retried is not None:
                    _logger.info(
                        "upstream 400 retry: mode=%s body_keys=%s",
                        [p[0].__name__ for p in self._RETRY_RULES if p[0](self, body, error_text)],
                        list(retried.keys()),
                    )
                    async with httpx.AsyncClient(timeout=timeout) as client:
                        response = await client.post(url, headers=headers, json=retried)
                else:
                    _logger.warning("upstream 400 with no compatible retry: %.200s", error_text)
            response.raise_for_status()
            return response.json()

    async def stream_chat_completion(self, payload: Any) -> AsyncIterator[bytes]:
        async with httpx.AsyncClient(timeout=self._settings.upstream_timeout_seconds) as client:
            async with client.stream(
                "POST",
                self._chat_completions_url(),
                headers=self._headers(),
                json=payload.model_dump(mode="json", exclude_none=True),
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    if chunk:
                        yield chunk

    async def list_models(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self._settings.upstream_timeout_seconds) as client:
            response = await client.get(self._models_url(), headers=self._headers())
            response.raise_for_status()
            body = response.json()
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            raise RuntimeError("Upstream /v1/models did not return an OpenAI-style list")
        return [item for item in data if isinstance(item, dict)]
