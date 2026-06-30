from __future__ import annotations

from dataclasses import dataclass, field
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


# Canonical env var → Settings field mapping.
_ENV_MAP: dict[str, str] = {
    "BRIDGE_UPSTREAM_BASE_URL": "upstream_base_url",
    "BRIDGE_UPSTREAM_API_KEY": "upstream_api_key",
    "BRIDGE_UPSTREAM_TIMEOUT_SECONDS": "upstream_timeout_seconds",
    "BRIDGE_UPSTREAM_STREAMING": "upstream_streaming",
    "BRIDGE_UPSTREAM_MAX_RETRIES": "upstream_max_retries",
    "BRIDGE_MAX_CONCURRENT_REQUESTS": "max_concurrent_requests",
    "BRIDGE_UNSUPPORTED_TOOL_POLICY": "unsupported_tool_policy",
}


class _UnsetSentinel:
    """Singleton sentinel distinguishing 'not provided' from None or empty.

    Stored in the ``_explicitly_set`` set when a caller passes a value
    explicitly to ``Settings(...)`` — this lets __post_init__ skip
    env-var loading for those fields.
    """

    _instance: _UnsetSentinel | None = None

    def __new__(cls) -> _UnsetSentinel:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"


UNSET = _UnsetSentinel()


def _env_value(field: str) -> Any:
    """Load a field value from its canonical env var, applying defaults."""
    env_key = next(k for k, v in _ENV_MAP.items() if v == field)

    if field == "upstream_base_url":
        return _str_env(env_key, "").rstrip("/")
    if field == "upstream_api_key":
        return _str_env(env_key, "")
    if field == "upstream_timeout_seconds":
        return _float_env(env_key, 60.0)
    if field == "upstream_streaming":
        return _bool_env(env_key, True)
    if field == "upstream_max_retries":
        return _int_env(env_key, 2)
    if field == "max_concurrent_requests":
        return _int_env(env_key, 20)
    if field == "unsupported_tool_policy":
        return _str_env(env_key, "ignore").strip().lower()

    raise ValueError(f"Unknown settings field: {field}")


@dataclass(slots=True)
class Settings:
    """Bridge configuration derived from env vars with explicit-override support.

    Fields default to the UNSET sentinel.  ``__post_init__`` replaces any
    UNSET field with its env-var derived value.  Callers that pass an
    explicit value (including None or empty string) bypass env loading
    for that field.
    """

    upstream_base_url: str | _UnsetSentinel = field(default_factory=lambda: UNSET)
    upstream_api_key: str | _UnsetSentinel = field(default_factory=lambda: UNSET)
    upstream_timeout_seconds: float | _UnsetSentinel = field(default_factory=lambda: UNSET)
    upstream_streaming: bool | _UnsetSentinel = field(default_factory=lambda: UNSET)
    upstream_max_retries: int | _UnsetSentinel = field(default_factory=lambda: UNSET)
    max_concurrent_requests: int | _UnsetSentinel = field(default_factory=lambda: UNSET)
    unsupported_tool_policy: str | _UnsetSentinel = field(default_factory=lambda: UNSET)

    _explicitly_set: set[str] = field(default_factory=set, init=False, repr=False)

    def __post_init__(self) -> None:
        for field_name in _ENV_MAP.values():
            current = getattr(self, field_name)
            if isinstance(current, _UnsetSentinel):
                object.__setattr__(self, field_name, _env_value(field_name))
            else:
                # Caller provided an explicit value — record it so get_settings()
                # and validate_config() know this was not env-derived.
                self._explicitly_set.add(field_name)

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

    # After __post_init__, no field should still be UNSET.
    assert not isinstance(settings.upstream_base_url, _UnsetSentinel)
    assert not isinstance(settings.upstream_timeout_seconds, _UnsetSentinel)
    assert not isinstance(settings.upstream_max_retries, _UnsetSentinel)
    assert not isinstance(settings.max_concurrent_requests, _UnsetSentinel)

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
