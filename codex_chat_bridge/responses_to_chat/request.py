from __future__ import annotations

from typing import Any

from ..bridge_context import BridgeToolContext, TOOL_SEARCH_PROXY_NAME, build_tool_context_from_request
from ..config import ReasoningMode, get_settings
from ..models import ChatCompletionsRequest, ChatMessage, ResponsesRequest
from .common import (
    BUILT_IN_RESPONSES_TOOLS,
    EXTRA_CHAT_PASSTHROUGH_FIELDS,
    _sanitize_chat_messages,
    collapse_system_messages_to_head,
    instruction_text,
    is_openai_o_series,
)
from .errors import UnsupportedResponsesInputItemError
from .items import append_input_items_as_chat_messages


def _reasoning_effort(payload: ResponsesRequest) -> str | None:
    """Extract normalized reasoning effort string from the Responses request."""
    if payload.reasoning is None:
        return None
    effort = payload.reasoning.get("effort")
    if not isinstance(effort, str):
        return None
    normalized = effort.strip().lower()
    if normalized in {"none", "off", "disabled"}:
        return "none"
    return normalized


def _reasoning_enabled(payload: ResponsesRequest) -> bool | None:
    """Whether reasoning is explicitly enabled (None means not specified)."""
    effort = _reasoning_effort(payload)
    if effort is None:
        return None
    return effort != "none"


# ---- reasoning mode handlers (called via dispatch table) ----


def _noop_reasoning(
    payload: ResponsesRequest, request: ChatCompletionsRequest, effort: str | None, enabled: bool | None
) -> None:
    """完全禁用推理参数，不发送任何 reasoning/thinking 字段。"""
    return


def _passthrough_reasoning(
    payload: ResponsesRequest, request: ChatCompletionsRequest, effort: str | None, enabled: bool | None
) -> None:
    """原样透传 reasoning 字段到 upstream。"""
    if payload.reasoning is not None:
        request.thinking = payload.reasoning


def _thinking_reasoning(
    payload: ResponsesRequest, request: ChatCompletionsRequest, effort: str | None, enabled: bool | None
) -> None:
    """DeepSeek: thinking.type + reasoning_effort"""
    if enabled is True:
        request.thinking = {"type": "enabled"}
    elif enabled is False:
        request.thinking = {"type": "disabled"}
    if effort and effort != "none":
        request.reasoning_effort = effort


def _thinking_only_reasoning(
    payload: ResponsesRequest, request: ChatCompletionsRequest, effort: str | None, enabled: bool | None
) -> None:
    """GLM/Kimi/MiMo: 仅 thinking.type，不含 reasoning_effort"""
    if enabled is True:
        request.thinking = {"type": "enabled"}
    elif enabled is False:
        request.thinking = {"type": "disabled"}


def _enable_thinking_reasoning(
    payload: ResponsesRequest, request: ChatCompletionsRequest, effort: str | None, enabled: bool | None
) -> None:
    """SiliconFlow/Qwen: enable_thinking=true"""
    if enabled is True:
        request.thinking = {"type": "enabled"}


def _split_reasoning(
    payload: ResponsesRequest, request: ChatCompletionsRequest, effort: str | None, enabled: bool | None
) -> None:
    """MiniMax: reasoning_split=true"""
    if enabled is True:
        request.thinking = {"type": "enabled"}


def _effort_obj_reasoning(
    payload: ResponsesRequest, request: ChatCompletionsRequest, effort: str | None, enabled: bool | None
) -> None:
    """OpenRouter: reasoning={effort: ...}"""
    if effort and effort != "none":
        request.thinking = {"type": "enabled"}
        request.reasoning_effort = effort


def _effort_reasoning(
    payload: ResponsesRequest, request: ChatCompletionsRequest, effort: str | None, enabled: bool | None
) -> None:
    """OpenAI 标准: reasoning.effort -> reasoning_effort"""
    if enabled is None:
        return
    request.thinking = {"type": "enabled" if enabled else "disabled"}
    if effort and effort != "none":
        request.reasoning_effort = effort


def _apply_reasoning_options(payload: ResponsesRequest, request: ChatCompletionsRequest) -> None:
    reasoning_mode = get_settings().reasoning_mode
    effort = _reasoning_effort(payload)
    enabled = _reasoning_enabled(payload)

    _dispatcher = {
        ReasoningMode.NONE: _noop_reasoning,
        ReasoningMode.PASSTHROUGH: _passthrough_reasoning,
        ReasoningMode.THINKING: _thinking_reasoning,
        ReasoningMode.THINKING_ONLY: _thinking_only_reasoning,
        ReasoningMode.ENABLE_THINKING: _enable_thinking_reasoning,
        ReasoningMode.SPLIT: _split_reasoning,
        ReasoningMode.EFFORT_OBJ: _effort_obj_reasoning,
    }
    handler = _dispatcher.get(reasoning_mode, _effort_reasoning)
    handler(payload, request, effort, enabled)


def _responses_tool_to_chat_tool(tool: dict[str, Any]) -> dict[str, Any] | None:
    tool_type = tool.get("type")
    if tool_type == "tool_search":
        return {
            "type": "function",
            "function": {
                "name": TOOL_SEARCH_PROXY_NAME,
                "description": "Search and load Codex tools, plugins, connectors, and MCP namespaces for the current task.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query for tools or connectors to load.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of tool groups to return.",
                        },
                    },
                    "required": ["query"],
                },
            },
        }
    # 检查是否为无法转换的内置工具
    if tool_type in BUILT_IN_RESPONSES_TOOLS:
        policy = get_settings().unsupported_tool_policy
        if policy == "error":
            raise UnsupportedResponsesInputItemError(tool_type, tool)
        # policy == "ignore" 静默跳过
        return None
    return None


def _responses_tool_choice_to_chat(tool_choice: Any, tool_context: BridgeToolContext) -> Any:
    if not isinstance(tool_choice, dict):
        return tool_choice
    choice_type = tool_choice.get("type")
    if choice_type == "tool_search":
        return {"type": "function", "function": {"name": TOOL_SEARCH_PROXY_NAME}}
    if choice_type in {"function", "custom"}:
        name = tool_choice.get("name")
        namespace = tool_choice.get("namespace") if isinstance(tool_choice.get("namespace"), str) else None
        if isinstance(name, str) and name:
            return {"type": "function", "function": {"name": tool_context.chat_name_for_function(name, namespace)}}
    return tool_choice


def _response_format_from_payload(payload: ResponsesRequest) -> Any:
    if isinstance(payload.text, dict) and isinstance(payload.text.get("format"), dict):
        return payload.text.get("format")
    return payload.response_format


def responses_to_chat_request(payload: ResponsesRequest, default_model: str, tool_context: BridgeToolContext | None = None) -> ChatCompletionsRequest:
    messages: list[ChatMessage] = []

    if payload.instructions:
        instructions = instruction_text(payload.instructions).strip()
        if instructions:
            messages.append(ChatMessage(role="system", content=instructions))

    tool_context = tool_context or build_tool_context_from_request(payload)
    append_input_items_as_chat_messages(payload, messages, tool_context)
    messages = collapse_system_messages_to_head(messages)

    stream_options = {"include_usage": True} if payload.stream else None
    chat_tools = tool_context.chat_tools or None
    model_name = payload.model or default_model

    request = ChatCompletionsRequest(
        model=model_name,
        messages=_sanitize_chat_messages(messages),
        stream=payload.stream,
        stream_options=stream_options,
        tools=chat_tools,
        tool_choice=_responses_tool_choice_to_chat(payload.tool_choice, tool_context) if chat_tools and payload.tool_choice is not None else None,
        response_format=_response_format_from_payload(payload),
        max_tokens=None if is_openai_o_series(model_name) else payload.max_output_tokens,
        temperature=payload.temperature,
        top_p=payload.top_p,
    )
    if is_openai_o_series(model_name) and payload.max_output_tokens is not None:
        request.max_completion_tokens = payload.max_output_tokens
    for field in EXTRA_CHAT_PASSTHROUGH_FIELDS:
        if field == "response_format":
            continue
        value = getattr(payload, field, None)
        if value is not None:
            setattr(request, field, value)
    _apply_reasoning_options(payload, request)
    return request
