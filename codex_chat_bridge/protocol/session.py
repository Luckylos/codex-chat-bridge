"""会话存储 — 支持 previous_response_id 的有状态上下文管理。

保存 messages + tool_context 供后续请求恢复，实现 Responses API 的会话延续。
当前为单进程内存存储，TTL 惰性清理。如需多进程/持久化，替换 _sessions 后端即可。
"""

from __future__ import annotations

import copy
import logging
import time

from ..bridge_context import BridgeToolContext, build_tool_context_from_request
from ..models import ChatMessage, ResponsesRequest

_logger = logging.getLogger("codex-chat-bridge")


class SessionRecord:
    """一次 Responses 响应的状态快照。

    messages 和 tool_context 在构造时做深拷贝，确保后续
    请求对同一 response_id 的修改不会变异已持久化的历史。
    """

    __slots__ = ("messages", "tool_context", "model", "created_at", "last_accessed_at")

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
        self.last_accessed_at = self.created_at


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
        """查询会话，过期条目视为不存在。访问时自动续期 TTL。"""
        record = self._sessions.get(response_id)
        if record is None:
            return None
        # Use last_accessed_at so that frequent access keeps the session alive
        if time.time() - record.last_accessed_at > self._ttl:
            del self._sessions[response_id]
            return None
        # Renew TTL: bump last_accessed_at so the session stays alive on access
        record.last_accessed_at = time.time()
        # SessionRecord.__init__ deep-copies to isolate from caller mutations.
        # The stored record is already isolated (saved via copy), so constructing
        # a new SessionRecord here provides a safe independent copy to the caller.
        returned = SessionRecord(record.messages, record.tool_context, record.model, created_at=record.created_at)
        returned.last_accessed_at = record.last_accessed_at
        return returned

    def save(self, response_id: str, record: SessionRecord) -> None:
        """保存会话状态，同时触发惰性清理。

        Constructs a new SessionRecord (which deep-copies) so the stored
        data is fully isolated from the caller's references.  This way
        get() also returns a deep-copied SessionRecord, giving each
        consumer its own isolated snapshot.
        """
        now = time.time()
        # Deep-copy via SessionRecord constructor to isolate stored data
        new_record = SessionRecord(
            record.messages,
            record.tool_context,
            record.model,
            created_at=now,
        )
        new_record.last_accessed_at = now
        self._sessions[response_id] = new_record
        self._enforce_cap()
        self._cleanup()

    def _cleanup(self) -> None:
        """惰性清理过期条目（每次 get/save 触发）。"""
        now = time.time()
        stale = [rid for rid, rec in self._sessions.items() if now - rec.last_accessed_at > self._ttl]
        for rid in stale:
            del self._sessions[rid]

    def _enforce_cap(self) -> None:
        """超出上限时淘汰最旧条目（非过期）。"""
        while len(self._sessions) > self._max_sessions:
            oldest = min(self._sessions.items(), key=lambda kv: kv[1].last_accessed_at)[0]
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
    refusal = message.get("refusal")
    reasoning_content = message.get("reasoning_content") or message.get("reasoning")
    if not content and not tool_calls and not refusal and not reasoning_content:
        return None
    if not content:
        # Refusal text is semantically distinct from content, but ChatMessage
        # has no refusal field.  Drop refusal from session replay rather than
        # conflating it into the content field, and log for visibility.
        if refusal:
            _logger.debug("Dropping refusal from session-persisted assistant message: %r", refusal[:200])
        content = None
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
    merged = copy.deepcopy(existing)
    merged.merge(build_tool_context_from_request(payload))
    return merged


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
