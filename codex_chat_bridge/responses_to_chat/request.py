from __future__ import annotations

from typing import Any

from ..bridge_context import BridgeToolContext, TOOL_SEARCH_PROXY_NAME, build_tool_context_from_request
from ..models import ChatCompletionsRequest, ChatMessage, ResponsesRequest
from .common import (
    EXTRA_CHAT_PASSTHROUGH_FIELDS,
    collapse_system_messages_to_head,
    instruction_text,
    is_openai_o_series,
)
from .items import append_input_items_as_chat_messages


def _reasoning_requested(payload: ResponsesRequest) -> bool | None:
    if payload.reasoning is None:
        return None
    effort = payload.reasoning.get("effort")
    if isinstance(effort, str):
        return effort.strip().lower() not in {"none", "off", "disabled"}
    return True


def _map_reasoning_effort(payload: ResponsesRequest) -> str | None:
    if payload.reasoning is None:
        return None
    effort = payload.reasoning.get("effort")
    if not isinstance(effort, str):
        return None
    normalized = effort.strip().lower()
    if normalized in {"off", "disabled"}:
        return "none"
    return normalized if normalized else None


def _apply_reasoning_options(payload: ResponsesRequest, request: ChatCompletionsRequest) -> None:
    reasoning_enabled = _reasoning_requested(payload)
    if reasoning_enabled is None:
        return
    request.thinking = {"type": "enabled" if reasoning_enabled else "disabled"}
    mapped_effort = _map_reasoning_effort(payload)
    if mapped_effort is not None:
        request.reasoning_effort = mapped_effort


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
        messages=messages,
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
