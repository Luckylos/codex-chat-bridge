from __future__ import annotations

from typing import Any

from ..bridge_context import BridgeToolContext, canonical_json_string, iter_request_input_items
from ..models import ChatMessage, ResponsesRequest
from ..response_semantics import canonicalize_tool_arguments
from .errors import UnsupportedResponsesInputItemError

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

# 明确列出 Responses 内置工具类型，bridge 无法转发给 Chat Completions 上游
# 这些工具需要上游原生支持或由客户端直接调用
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

# 仅允许 https:// 和 data:image/ 两种 image URL scheme
# 拒绝 file://, http://(SSRF 风险), ftp:// 等
_ALLOWED_IMAGE_SCHEMES = ("https://", "data:image/")


def is_safe_image_url(url: str | None) -> bool:
    """检查 image URL 是否安全，防止 SSRF 和内网泄露。

    允许 scheme:
      - https://        — 标准外链
      - data:image/     — inline base64 图片

    拒绝:
      - file://         — 本地文件读取
      - http://         — 内网/云元数据攻击向量
      - ftp:// 等
    """
    if not isinstance(url, str) or not url:
        return False
    return url.startswith(_ALLOWED_IMAGE_SCHEMES)


def _sanitize_chat_messages(messages: list[ChatMessage]) -> list[ChatMessage]:
    """三段式消息归一化流水线。

    1. 去空: 过滤掉 content 为空且没有 tool_calls 的消息
    2. 合并: 合并相邻同 role 消息（避免上游拒绝连续 user→user）
    3. 角色归一: 确保 role 值合规
    """
    if not messages:
        return messages

    # Step 1: 去空 — 仅过滤完全空白的 assistant/tool 消息
    sanitized: list[ChatMessage] = []
    for msg in messages:
        has_content = msg.content is not None and msg.content != "" and not (
            isinstance(msg.content, list) and not msg.content
        )
        has_tool_calls = bool(msg.tool_calls)
        has_tool_call_id = bool(msg.tool_call_id)
        # 保留 user/system 即使 content 为空（上游可能需要 context 占位）
        if msg.role in ("user", "system"):
            sanitized.append(msg)
        elif has_content or has_tool_calls or has_tool_call_id:
            sanitized.append(msg)
    if not sanitized:
        return sanitized

    # Step 2: 合并相邻同 role 消息
    merged: list[ChatMessage] = [sanitized[0]]
    for msg in sanitized[1:]:
        prev = merged[-1]
        if _messages_are_mergeable(prev, msg):
            merged[-1] = _merge_messages(prev, msg)
        else:
            merged.append(msg)

    # Step 3: role 合规检查 — 不强制插入占位，交给上游处理

    return merged


def _messages_are_mergeable(a: ChatMessage, b: ChatMessage) -> bool:
    """两条消息是否可以合并
    当前仅 system 消息需要合并（collapse_system_messages_to_head 已处理）。
    """
    if a.role != b.role:
        return False
    return a.role in ("system",)


def _merge_messages(a: ChatMessage, b: ChatMessage) -> ChatMessage:
    """合并两条相邻同 role 消息"""
    merged_content = _merge_content(a.content, b.content)
    if a.role == "tool":
        return ChatMessage(role="tool", content=merged_content)
    return ChatMessage(role=a.role, content=merged_content)


def _merge_content(a: Any, b: Any) -> str | list | None:
    """合并两条消息的内容字段"""
    a_text = flatten_text_content(a).strip()
    b_text = flatten_text_content(b).strip()
    merged = "\n\n".join(p for p in [a_text, b_text] if p)
    return merged or None


def normalize_tool_output_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if isinstance(value.get("content"), list):
            flattened = flatten_text_content(value.get("content"))
            if flattened:
                return flattened
        if value.get("type") in {"input_text", "output_text", "text"} and isinstance(value.get("text"), str):
            return value["text"]
    if isinstance(value, list):
        flattened = flatten_text_content(value)
        if flattened:
            return flattened
    return canonical_json_string(value)


def instruction_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        chunks: list[str] = []
        for part in value:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                if part["text"]:
                    chunks.append(part["text"])
            elif isinstance(part, str) and part:
                chunks.append(part)
        return "\n\n".join(chunks)
    return str(value) if value is not None else ""


def is_openai_o_series(model: str | None) -> bool:
    if not isinstance(model, str):
        return False
    normalized = model.strip().lower()
    return normalized.startswith("o1") or normalized.startswith("o3") or normalized.startswith("o4")


def flatten_text_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    chunks: list[str] = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                typ = item.get("type")
                if typ in {"input_text", "output_text", "text"} and isinstance(item.get("text"), str):
                    chunks.append(item["text"])
            elif isinstance(item, str):
                chunks.append(item)
    return "\n".join(chunk for chunk in chunks if chunk)


def chat_image_part_from_input_item(item: dict[str, Any]) -> dict[str, Any]:
    image_value = item.get("image_url")
    if isinstance(image_value, str) and image_value:
        url = image_value
    elif isinstance(image_value, dict) and isinstance(image_value.get("url"), str) and image_value.get("url"):
        url = image_value["url"]
        payload = dict(image_value)
    else:
        raise UnsupportedResponsesInputItemError(item.get("type") if isinstance(item.get("type"), str) else None, item)
    if not is_safe_image_url(url):
        raise UnsupportedResponsesInputItemError(
            item.get("type") if isinstance(item.get("type"), str) else None,
            item,
            detail=f"Rejected unsafe image URL scheme (only https:// and data:image/ allowed): {url[:60]}",
        )
    if isinstance(image_value, dict):
        payload = dict(image_value)
    else:
        payload: dict[str, Any] = {"url": url}
    detail = item.get("detail")
    if isinstance(detail, str) and detail and "detail" not in payload:
        payload["detail"] = detail
    return {"type": "image_url", "image_url": payload}


def chat_message_content_from_response_content(content: Any) -> str | list[dict[str, Any]] | None:
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return flatten_text_content(content)
    parts: list[dict[str, Any]] = []
    has_non_text = False
    for item in content:
        if isinstance(item, str):
            if item:
                parts.append({"type": "text", "text": item})
            continue
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type in {"input_text", "output_text", "text"} and isinstance(item.get("text"), str):
            parts.append({"type": "text", "text": item["text"]})
            continue
        if item_type == "refusal" and isinstance(item.get("refusal"), str):
            if item.get("refusal"):
                parts.append({"type": "text", "text": item["refusal"]})
            continue
        if item_type == "input_image":
            try:
                image_part = chat_image_part_from_input_item(item)
            except UnsupportedResponsesInputItemError:
                continue
            has_non_text = True
            parts.append(image_part)
            continue
        continue
    if not parts:
        return ""
    if not has_non_text and all(part.get("type") == "text" for part in parts):
        return "\n".join(part["text"] for part in parts if isinstance(part.get("text"), str) and part.get("text"))
    return parts


def reasoning_item_text(item: dict[str, Any]) -> str:
    summary = item.get("summary")
    if isinstance(summary, list):
        chunks: list[str] = []
        for part in summary:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                chunks.append(part["text"])
        return "\n\n".join(chunk for chunk in chunks if chunk)
    if isinstance(item.get("text"), str):
        return item["text"]
    return ""


def normalize_message_tool_calls(value: Any, tool_context: BridgeToolContext) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    normalized: list[dict[str, Any]] = []
    for index, tool_call in enumerate(value):
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else tool_call
        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        namespace = function.get("namespace") if isinstance(function.get("namespace"), str) else None
        normalized.append(
            {
                "id": str(tool_call.get("id") or tool_call.get("call_id") or f"call_{index}"),
                "type": "function",
                "function": {
                    "name": tool_context.chat_name_for_function(name, namespace),
                    "arguments": canonicalize_tool_arguments(function.get("arguments")),
                },
            }
        )
    return normalized or None


def message_has_tool_calls(message: ChatMessage) -> bool:
    return bool(message.role == "assistant" and message.tool_calls)


def append_reasoning_to_last_assistant(messages: list[ChatMessage], reasoning: str) -> bool:
    reasoning = reasoning.strip()
    if not reasoning:
        return False
    for message in reversed(messages):
        if message.role != "assistant":
            continue
        if message.reasoning_content and message.reasoning_content.strip():
            message.reasoning_content = message.reasoning_content.strip() + "\n\n" + reasoning
            return True
        message.reasoning_content = reasoning
        return True
    return False


def ensure_tool_call_reasoning_content(message: ChatMessage) -> None:
    if message_has_tool_calls(message) and not (message.reasoning_content and message.reasoning_content.strip()):
        message.reasoning_content = "tool call"


def backfill_tool_call_reasoning_content(messages: list[ChatMessage]) -> None:
    for message in messages:
        ensure_tool_call_reasoning_content(message)


def collapse_system_messages_to_head(messages: list[ChatMessage]) -> list[ChatMessage]:
    system_chunks: list[str] = []
    rest: list[ChatMessage] = []
    for message in messages:
        if message.role == "system":
            text = flatten_text_content(message.content).strip()
            if text:
                system_chunks.append(text)
            continue
        rest.append(message)
    if not system_chunks:
        return rest
    return [ChatMessage(role="system", content="\n\n".join(system_chunks))] + rest


def iter_input_items(payload: ResponsesRequest) -> list[Any]:
    return iter_request_input_items(payload)
