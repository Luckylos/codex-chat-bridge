from __future__ import annotations

from dataclasses import dataclass
import os


class ReasoningMode:
    """上游推理(reasoning)参数映射模式。

    枚举值对应不同上游厂商支持的推理参数格式：
      passthrough     — 不处理，原样透传 reasoning 字段
      effort          — mapping: reasoning.effort -> reasoning_effort (OpenAI 标准)
      thinking        — mapping: thinking.type + reasoning_effort (DeepSeek)
      thinking_only   — mapping: thinking.type 仅，不含 reasoning_effort (GLM/Kimi/MiMo)
      enable_thinking — mapping: enable_thinking=true (SiliconFlow/Qwen)
      split           — mapping: reasoning_split=true (MiniMax)
      effort_obj      — mapping: reasoning={effort: ...} (OpenRouter)
      none            — 禁用所有推理参数映射（发送不带 reasoning 的请求）
    """
    PASSTHROUGH = "passthrough"
    EFFORT = "effort"
    THINKING = "thinking"
    THINKING_ONLY = "thinking_only"
    ENABLE_THINKING = "enable_thinking"
    SPLIT = "split"
    EFFORT_OBJ = "effort_obj"
    NONE = "none"

    _VALID = {PASSTHROUGH, EFFORT, THINKING, THINKING_ONLY,
              ENABLE_THINKING, SPLIT, EFFORT_OBJ, NONE}

    @classmethod
    def normalize(cls, raw: str | None) -> str:
        normalized = (raw or "").strip().lower()
        return normalized if normalized in cls._VALID else cls.EFFORT


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
    unsupported_tool_policy: str = "ignore"
    public_base_url: str = "http://127.0.0.1:18090/v1"
    reasoning_mode: str = ReasoningMode.EFFORT

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
        if self.reasoning_mode == ReasoningMode.EFFORT:
            object.__setattr__(self, "reasoning_mode", ReasoningMode.normalize(os.getenv("BRIDGE_REASONING_MODE")))
        if self.public_base_url == "http://127.0.0.1:18090/v1":
            object.__setattr__(self, "public_base_url", _str_env("BRIDGE_PUBLIC_BASE_URL", "http://127.0.0.1:18090/v1"))


def get_settings() -> Settings:
    return Settings()
