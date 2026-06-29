"""会话存储 — 支持 previous_response_id 的有状态上下文管理。

保存 messages + tool_context 供后续请求恢复，实现 Responses API 的会话延续。
当前为单进程内存存储，TTL 惰性清理。如需多进程/持久化，替换 _sessions 后端即可。
"""

from __future__ import annotations

import copy
import time
from typing import Any

from .bridge_context import BridgeToolContext, build_tool_context_from_request
from .models import ChatMessage, ResponsesRequest


class SessionRecord:
    """一次 Responses 响应的状态快照。

    messages 和 tool_context 在构造时做深拷贝，确保后续
    请求对同一 response_id 的修改不会变异已持久化的历史。
    """

    __slots__ = ("messages", "tool_context", "model", "created_at")

    def __init__(
        self,
        messages: list[ChatMessage],
        tool_context: BridgeToolContext,
        model: str,
        created_at: float | None = None,
    ) -> None:
        # Deep-copy to isolate from caller mutations
        self.messages: list[ChatMessage] = copy.deepcopy(messages)
        self.tool_context: BridgeToolContext = copy.deepcopy(tool_context)
        self.model = model
        self.created_at = created_at or time.time()


_DEFAULT_TTL = 3600  # 1 hour


class SessionStore:
    """In-memory 会话存储，按 response_id 索引。"""

    def __init__(self, ttl: int = _DEFAULT_TTL, max_sessions: int = 500) -> None:
        self._ttl = ttl
        self._max_sessions = max_sessions
        self._sessions: dict[str, SessionRecord] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, response_id: str) -> SessionRecord | None:
        """查询会话，过期条目视为不存在。"""
        record = self._sessions.get(response_id)
        if record is None:
            return None
        if time.time() - record.created_at > self._ttl:
            del self._sessions[response_id]
            return None
        return record

    def save(self, response_id: str, record: SessionRecord) -> None:
        """保存会话状态，同时触发惰性清理。"""
        record.created_at = time.time()
        self._sessions[response_id] = record
        self._enforce_cap()
        self._cleanup()

    def _cleanup(self) -> None:
        """惰性清理过期条目（每次 get/save 触发）。"""
        now = time.time()
        stale = [rid for rid, rec in self._sessions.items() if now - rec.created_at > self._ttl]
        for rid in stale:
            del self._sessions[rid]

    def _enforce_cap(self) -> None:
        """超出上限时淘汰最旧条目（非过期）。"""
        while len(self._sessions) > self._max_sessions:
            oldest = min(self._sessions.items(), key=lambda kv: kv[1].created_at)[0]
            del self._sessions[oldest]

    @property
    def active_count(self) -> int:
        """当前活跃会话数（调试/监控用）。"""
        return len(self._sessions)


# ------------------------------------------------------------------
# 桥接助手 — 整合 session 与 request 转换
# ------------------------------------------------------------------

_global_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    """全局 session store 单例。"""
    global _global_store
    if _global_store is None:
        _global_store = SessionStore()
    return _global_store


def _assistant_message_from_chat_body(chat_body: dict) -> ChatMessage | None:
    """从上游 Chat Completions 响应体中提取 assistant 消息，用于 session 持久化。"""
    choice = (chat_body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    if not message:
        return None
    role = message.get("role", "assistant")
    content = message.get("content")
    tool_calls = message.get("tool_calls")
    reasoning_content = message.get("reasoning_content") or message.get("reasoning")
    if not content and not tool_calls:
        return None
    return ChatMessage(
        role=role,  # type: ignore[arg-type]
        content=content,
        tool_calls=tool_calls if isinstance(tool_calls, list) else None,
        reasoning_content=reasoning_content if isinstance(reasoning_content, str) else None,
    )


def _merge_tool_contexts(
    existing: BridgeToolContext,
    payload: ResponsesRequest,
) -> BridgeToolContext:
    """Merge tools from a new request into an existing session's tool context.

    Preserves all tools from the previous session; adds new tools from
    the current request that aren't already registered.
    """
    new_context = build_tool_context_from_request(payload)
    # Add tools from the new request that aren't already in the session
    for chat_tool in new_context.chat_tools:
        fn_name = chat_tool.get("function", {}).get("name", "")
        if fn_name and fn_name not in existing._seen_chat_names:
            spec = new_context.chat_name_to_spec.get(fn_name)
            if spec is not None:
                existing.add_chat_tool(fn_name, spec, chat_tool)
    # Propagate tool_search flag if the new request enables it
    if new_context.tool_search_enabled and not existing.tool_search_enabled:
        existing.add_tool_search_tool()
    # Propagate custom tool names
    for name in new_context.custom_tool_names - existing.custom_tool_names:
        existing.custom_tool_names.add(name)
        if name not in existing.chat_name_to_spec:
            spec = new_context.chat_name_to_spec.get(name)
            if spec is not None:
                existing.chat_name_to_spec[name] = spec
    return existing


def resolve_session(
    payload: ResponsesRequest,
) -> tuple[list[ChatMessage] | None, BridgeToolContext | None, str | None]:
    """解析 previous_response_id，返回 (existing_messages, tool_context, model) 或 (None, None, None)。

    返回的 messages 是会话已保存的完整历史（深拷贝，可安全修改）。
    tool_context 已合并新请求的 tools。
    """
    prev_id = getattr(payload, "previous_response_id", None)
    if not prev_id:
        return None, None, None

    store = get_session_store()
    record = store.get(prev_id)
    if record is None:
        return None, None, None

    # Merge new request tools into the session's tool context
    merged_context = _merge_tool_contexts(record.tool_context, payload)

    return record.messages, merged_context, record.model


def save_session(
    response_id: str,
    messages: list[ChatMessage],
    tool_context: BridgeToolContext,
    model: str,
    assistant_message: ChatMessage | None = None,
) -> None:
    """保存会话快照。提供 assistant_message 时将其追加到 messages 后再持久化。

    SessionRecord 构造时会深拷贝 messages 和 tool_context，
    所以此处可以安全地先修改再传入。
    """
    saved_messages = [*messages, assistant_message] if assistant_message is not None else messages
    store = get_session_store()
    store.save(response_id, SessionRecord(saved_messages, tool_context, model))
