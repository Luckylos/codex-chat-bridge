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


@dataclass(slots=True)
class Settings:
    upstream_base_url: str = os.getenv("BRIDGE_UPSTREAM_BASE_URL", "").rstrip("/")
    upstream_api_key: str = os.getenv("BRIDGE_UPSTREAM_API_KEY", "")
    upstream_timeout_seconds: float = float(os.getenv("BRIDGE_UPSTREAM_TIMEOUT_SECONDS", "60"))
    upstream_streaming: bool = os.getenv("BRIDGE_UPSTREAM_STREAMING", "true").strip().lower() in ("1", "true", "yes")
    unsupported_tool_policy: str = os.getenv("BRIDGE_UNSUPPORTED_TOOL_POLICY", "ignore").strip().lower()
    public_base_url: str = os.getenv("BRIDGE_PUBLIC_BASE_URL", "http://127.0.0.1:18090/v1")
    reasoning_mode: str = ReasoningMode.normalize(os.getenv("BRIDGE_REASONING_MODE"))


def get_settings() -> Settings:
    return Settings()
