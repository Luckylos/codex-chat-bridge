from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from typing import Any

_logger = logging.getLogger("codex-chat-bridge")


def _bool_env(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes")


def _str_env(key: str, default: str) -> str:
    return os.getenv(key, default)


def _float_env(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None:
        return default
    return float(raw)


def _int_env(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None:
        return default
    return int(raw)


_UNSET: object = object()

# TODO: Reintroduce a public bridge URL setting only when a runtime feature
# actually consumes externally routable bridge URLs.

# Canonical env var → field mapping.
_ENV_MAP: dict[str, str] = {
    "BRIDGE_UPSTREAM_BASE_URL": "upstream_base_url",
    "BRIDGE_UPSTREAM_API_KEY": "upstream_api_key",
    "BRIDGE_UPSTREAM_TIMEOUT_SECONDS": "upstream_timeout_seconds",
    "BRIDGE_UPSTREAM_STREAMING": "upstream_streaming",
    "BRIDGE_UPSTREAM_MAX_RETRIES": "upstream_max_retries",
    "BRIDGE_MAX_CONCURRENT_REQUESTS": "max_concurrent_requests",
    "BRIDGE_UNSUPPORTED_TOOL_POLICY": "unsupported_tool_policy",
}

# Type-specific loaders keyed by field name.
_LOADERS: dict[str, type] = {
    "upstream_base_url": str,
    "upstream_api_key": str,
    "upstream_timeout_seconds": float,
    "upstream_streaming": bool,
    "upstream_max_retries": int,
    "max_concurrent_requests": int,
    "unsupported_tool_policy": str,
}


@dataclass(slots=True)
class Settings:
    upstream_base_url: str = _UNSET  # type: ignore[assignment]
    upstream_api_key: str = _UNSET  # type: ignore[assignment]
    upstream_timeout_seconds: float = _UNSET  # type: ignore[assignment]
    upstream_streaming: bool = _UNSET  # type: ignore[assignment]
    upstream_max_retries: int = _UNSET  # type: ignore[assignment]
    max_concurrent_requests: int = _UNSET  # type: ignore[assignment]
    unsupported_tool_policy: str = _UNSET  # type: ignore[assignment]

    def __post_init__(self) -> None:
        for env_key, field in _ENV_MAP.items():
            current = getattr(self, field)
            if current is not _UNSET:
                # Explicit value — keep it, don't overwrite from env.
                continue
            loader = _LOADERS[field]
            default = self._field_default(field)
            if loader is bool:
                value = _bool_env(env_key, default)  # type: ignore[arg-type]
            elif loader is float:
                value = _float_env(env_key, default)  # type: ignore[arg-type]
            elif loader is int:
                value = _int_env(env_key, default)  # type: ignore[arg-type]
            else:
                value = _str_env(env_key, default)  # type: ignore[arg-type]
                if field == "upstream_base_url":
                    value = value.rstrip("/")
                if field == "unsupported_tool_policy":
                    value = value.strip().lower()
            object.__setattr__(self, field, value)

    @staticmethod
    def _field_default(field: str) -> Any:
        """Canonical defaults for fields not explicitly set and not in env."""
        _DEFAULTS: dict[str, Any] = {
            "upstream_base_url": "",
            "upstream_api_key": "",
            "upstream_timeout_seconds": 60.0,
            "upstream_streaming": True,
            "upstream_max_retries": 2,
            "max_concurrent_requests": 20,
            "unsupported_tool_policy": "ignore",
        }
        return _DEFAULTS[field]

    @classmethod
    def from_env(cls) -> Settings:
        """Create a Settings instance populated entirely from env vars."""
        return cls()


_cached_settings: Settings | None = None


def get_settings() -> Settings:
    """Return a module-level singleton Settings instance.

    First call constructs from env; subsequent calls return the same
    object.  Tests that need isolated settings should construct
    ``Settings(...)`` directly with explicit field values.
    """
    global _cached_settings
    if _cached_settings is None:
        _cached_settings = Settings.from_env()
    return _cached_settings


def validate_config() -> None:
    """Validate core startup configuration before the first request."""
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

    upstream_url = settings.upstream_base_url
    _logger.info(
        "Config valid: upstream=%s timeout=%.0fs max_retries=%d concurrency=%d",
        upstream_url,
        settings.upstream_timeout_seconds,
        settings.upstream_max_retries,
        settings.max_concurrent_requests,
    )
