from __future__ import annotations

import random
from typing import Any, Callable

import httpx

_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


def retryable_status(status_code: int) -> bool:
    return status_code in _RETRYABLE_STATUSES


def retryable_exception(exc: Exception) -> bool:
    """Network-level errors that are safe to retry."""
    return isinstance(
        exc,
        (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.RemoteProtocolError,
            httpx.ReadError,
            httpx.WriteError,
        ),
    )


def backoff_delay(attempt: int, base: float = 0.5, max_delay: float = 30.0) -> float:
    """Exponential backoff with jitter: base * 2^attempt + random(0, base)."""
    delay = min(base * (2 ** attempt), max_delay)
    jitter = random.uniform(0, base)
    return delay + jitter


async def send_once(
    client_factory: Callable[..., httpx.AsyncClient],
    timeout_seconds: float,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    *,
    is_stream: bool,
) -> tuple[httpx.Response, httpx.AsyncClient | None]:
    if is_stream:
        client = client_factory(timeout=timeout_seconds)
        response = await client.send(
            client.build_request("POST", url, headers=headers, json=body),
            stream=True,
        )
        return response, client

    async with client_factory(timeout=timeout_seconds) as client:
        response = await client.post(url, headers=headers, json=body)
    return response, None


async def close_response_client(
    response: httpx.Response,
    client: httpx.AsyncClient | None,
) -> None:
    try:
        await response.aclose()
    finally:
        if client is not None:
            await client.aclose()


async def read_error_text(response: httpx.Response) -> str:
    raw = await response.aread()
    try:
        return raw.decode()
    except UnicodeDecodeError:
        return raw.decode(errors="replace")
