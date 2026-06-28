from __future__ import annotations

from dataclasses import dataclass
import os


def _bool_env(key: str, default: str) -> bool:
    return os.getenv(key, default).strip().lower() in ("1", "true", "yes")


def _str_env(key: str, default: str) -> str:
    return os.getenv(key, default)


def _float_env(key: str, default: str) -> float:
    return float(os.getenv(key, default))


@dataclass(slots=True)
class Settings:
    upstream_base_url: str = ""
    upstream_api_key: str = ""
    upstream_timeout_seconds: float = 60.0
    upstream_streaming: bool = True
    upstream_max_retries: int = 2
    max_concurrent_requests: int = 20
    unsupported_tool_policy: str = "ignore"
    public_base_url: str = "http://127.0.0.1:18090/v1"

    def __post_init__(self) -> None:
        # 模块级 env 用法: Settings() → 从环境变量读取
        # 也支持显式传值: Settings(upstream_base_url="http://...")
        if not self.upstream_base_url:
            object.__setattr__(self, "upstream_base_url", _str_env("BRIDGE_UPSTREAM_BASE_URL", "").rstrip("/"))
        if not self.upstream_api_key:
            object.__setattr__(self, "upstream_api_key", _str_env("BRIDGE_UPSTREAM_API_KEY", ""))
        if self.upstream_timeout_seconds == 60.0:
            object.__setattr__(self, "upstream_timeout_seconds", _float_env("BRIDGE_UPSTREAM_TIMEOUT_SECONDS", "60"))
        if self.upstream_streaming is True:
            object.__setattr__(self, "upstream_streaming", _bool_env("BRIDGE_UPSTREAM_STREAMING", "true"))
        if self.unsupported_tool_policy == "ignore":
            object.__setattr__(self, "unsupported_tool_policy", _str_env("BRIDGE_UNSUPPORTED_TOOL_POLICY", "ignore").strip().lower())
        if self.public_base_url == "http://127.0.0.1:18090/v1":
            object.__setattr__(self, "public_base_url", _str_env("BRIDGE_PUBLIC_BASE_URL", "http://127.0.0.1:18090/v1"))


def get_settings() -> Settings:
    return Settings()
