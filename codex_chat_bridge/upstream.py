from __future__ import annotations

from collections.abc import AsyncIterator
import json
from typing import Any

import httpx

from .config import Settings


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
    # 400 compatible retry — upstream 返回 400 时尝试兼容重试
    # ------------------------------------------------------------------

    _COMPATIBLE_RETRY_PATTERNS: list[dict] = [
        # Ali DashScope: top_p must be in (0,1) open interval
        {"check": lambda b, r: b.get("top_p") is not None and b["top_p"] >= 1,
         "apply": lambda b: {**b, "top_p": 0.999}},
        # stream_options {include_usage: true} on non-streaming requests
        {"check": lambda b, r: b.get("stream") is True and b.get("stream_options") is not None,
         "apply": lambda b: {**b, "stream_options": None}},
        # Unknown field rejected; try stripping stream_options entirely
        {"check": lambda b, r: b.get("stream_options") is not None,
         "apply": lambda b: {**b, "stream_options": None}},
        # Some upstreams reject stream_options.include_usage
        {"check": lambda b, r: isinstance(b.get("stream_options"), dict) and "include_usage" in b["stream_options"],
         "apply": lambda b: {**b, "stream_options": {"include_usage": False}}},
        # Parallel_tool_calls not supported
        {"check": lambda b, r: b.get("parallel_tool_calls") is not None,
         "apply": lambda b: {**b, "parallel_tool_calls": None}},
    ]

    def _has_compatible_retry(self, body: dict, error_text: str) -> bool:
        return any(p["check"](body, error_text) for p in self._COMPATIBLE_RETRY_PATTERNS)

    def _apply_compatible_body(self, body: dict, error_text: str) -> dict:
        result = dict(body)
        for p in self._COMPATIBLE_RETRY_PATTERNS:
            if p["check"](result, error_text):
                result = p["apply"](result)
        return result

    async def create_chat_completion(self, payload: Any) -> dict[str, Any]:
        body = payload.model_dump(mode="json", exclude_none=True)
        url = self._chat_completions_url()
        headers = self._headers()
        timeout = self._settings.upstream_timeout_seconds

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=body)
            if response.status_code == 400 and self._has_compatible_retry(body, response.text):
                body = self._apply_compatible_body(body, response.text)
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(url, headers=headers, json=body)
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
