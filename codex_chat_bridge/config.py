from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(slots=True)
class Settings:
    upstream_base_url: str = os.getenv("BRIDGE_UPSTREAM_BASE_URL", "").rstrip("/")
    upstream_api_key: str = os.getenv("BRIDGE_UPSTREAM_API_KEY", "")
    upstream_timeout_seconds: float = float(os.getenv("BRIDGE_UPSTREAM_TIMEOUT_SECONDS", "60"))
    public_base_url: str = os.getenv("BRIDGE_PUBLIC_BASE_URL", "http://127.0.0.1:18090/v1")


def get_settings() -> Settings:
    return Settings()
