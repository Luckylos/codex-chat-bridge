from __future__ import annotations

import logging

from .config import Settings, get_settings
from .upstream import UpstreamClient

_logger = logging.getLogger("codex-chat-bridge")


def validate_config() -> None:
    """验证核心配置在启动时是否合理，而非第一次请求才发现。

    Raises RuntimeError if config is invalid.
    """
    settings = get_settings()

    if not settings.upstream_base_url:
        raise RuntimeError(
            "BRIDGE_UPSTREAM_BASE_URL is required but not set. "
            "Example: BRIDGE_UPSTREAM_BASE_URL=https://newapi.example.com/v1"
        )

    if not settings.upstream_api_key:
        _logger.warning("BRIDGE_UPSTREAM_API_KEY is not set — upstream requests will lack Bearer auth.")

    if settings.upstream_timeout_seconds <= 0:
        raise RuntimeError(f"BRIDGE_UPSTREAM_TIMEOUT_SECONDS must be > 0, got {settings.upstream_timeout_seconds}")

    if settings.upstream_max_retries < 0:
        raise RuntimeError(f"BRIDGE_UPSTREAM_MAX_RETRIES must be >= 0, got {settings.upstream_max_retries}")

    if settings.max_concurrent_requests < 1:
        raise RuntimeError(f"BRIDGE_MAX_CONCURRENT_REQUESTS must be >= 1, got {settings.max_concurrent_requests}")

    upstream_url = settings.upstream_base_url.rstrip("/")
    _logger.info(
        "Config valid: upstream=%s timeout=%.0fs max_retries=%d concurrency=%d",
        upstream_url, settings.upstream_timeout_seconds,
        settings.upstream_max_retries, settings.max_concurrent_requests,
    )


def check_upstream_connectivity() -> bool:
    """尝试列出上游模型，验证网络连通性。返回 True 表示上游可达。"""
    try:
        import asyncio
        models = asyncio.run(UpstreamClient(get_settings()).list_models())
        _logger.info("Upstream reachable: %d models returned", len(models))
        return True
    except Exception as exc:
        _logger.warning("Upstream connectivity check failed: %s", exc)
        return False
