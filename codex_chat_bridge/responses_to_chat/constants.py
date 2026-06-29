from __future__ import annotations

EXTRA_CHAT_PASSTHROUGH_FIELDS = (
    "frequency_penalty",
    "logit_bias",
    "logprobs",
    "metadata",
    "n",
    "parallel_tool_calls",
    "presence_penalty",
    "response_format",
    "seed",
    "service_tier",
    "stop",
    "stream_options",
    "top_logprobs",
    "user",
)

BUILT_IN_RESPONSES_TOOLS = {
    "web_search",
    "web_search_preview",
    "file_search",
    "computer_use",
    "computer_use_preview",
    "code_interpreter",
    "image_generation",
    "mcp",
}


def is_openai_o_series(model: str | None) -> bool:
    if not isinstance(model, str):
        return False
    normalized = model.strip().lower()
    return normalized.startswith("o1") or normalized.startswith("o3") or normalized.startswith("o4")
