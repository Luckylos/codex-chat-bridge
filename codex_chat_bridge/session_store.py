"""会话存储 — 支持 previous_response_id 的有状态上下文管理。

保存 messages + tool_context 供后续请求恢复，实现 Responses API 的会话延续。
当前为单进程内存存储，TTL 惰性清理。如需多进程/持久化，替换 _sessions 后端即可。
"""

from __future__ import annotations

import time
from typing import Any

from .bridge_context import BridgeToolContext, build_tool_context_from_request
from .models import ChatMessage, ResponsesRequest


class SessionRecord:
    """一次 Responses 响应的状态快照。"""

    __slots__ = ("messages", "tool_context", "model", "created_at")

    def __init__(
        self,
        messages: list[ChatMessage],
        tool_context: BridgeToolContext,
        model: str,
        created_at: float | None = None,
    ) -> None:
        self.messages = messages
        self.tool_context = tool_context
        self.model = model
        self.created_at = created_at or time.time()


_DEFAULT_TTL = 3600  # 1 hour


class SessionStore:
    """In-memory 会话存储，按 response_id 索引。"""

    def __init__(self, ttl: int = _DEFAULT_TTL) -> None:
        self._ttl = ttl
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
        self._cleanup()

    def _cleanup(self) -> None:
        """惰性清理过期条目（每次 get/save 触发）。"""
        now = time.time()
        stale = [rid for rid, rec in self._sessions.items() if now - rec.created_at > self._ttl]
        for rid in stale:
            del self._sessions[rid]

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


def resolve_session(
    payload: ResponsesRequest,
) -> tuple[list[ChatMessage] | None, BridgeToolContext | None, str | None]:
    """解析 previous_response_id，返回 (existing_messages, tool_context, model) 或 (None, None, None)。

    返回的 messages 是会话已保存的完整历史。调用方应在其基础上追加新的 input items。
    """
    prev_id = getattr(payload, "previous_response_id", None)
    if not prev_id:
        return None, None, None

    store = get_session_store()
    record = store.get(prev_id)
    if record is None:
        return None, None, None

    return record.messages, record.tool_context, record.model


def save_session(
    response_id: str,
    messages: list[ChatMessage],
    tool_context: BridgeToolContext,
    model: str,
    assistant_message: ChatMessage | None = None,
) -> None:
    """保存会话快照。提供 assistant_message 时将其追加到 messages 后再持久化。"""
    saved_messages = [*messages, assistant_message] if assistant_message is not None else messages
    store = get_session_store()
    store.save(response_id, SessionRecord(saved_messages, tool_context, model))