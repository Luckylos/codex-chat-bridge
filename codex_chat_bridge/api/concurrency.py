from __future__ import annotations

import asyncio

from ..config import get_settings

_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        count = get_settings().max_concurrent_requests
        _semaphore = asyncio.Semaphore(count)
    return _semaphore


def reset_semaphore(count: int | None = None) -> None:
    """Reset semaphore (for testing)."""
    global _semaphore
    _semaphore = asyncio.Semaphore(count) if count is not None else None