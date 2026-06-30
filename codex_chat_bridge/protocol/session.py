"""Session store — stateful context management for previous_response_id.

Persists messages + tool_context for later request recovery, enabling
Responses API session continuity.  Currently a single-process in-memory
store with lazy TTL cleanup.  For multi-process/persistent backends,
replace the _sessions backend.
"""

from __future__ import annotations

import copy
import logging
import time
from typing import Literal, cast

from ..bridge_context import BridgeToolContext, build_tool_context_from_request
from ..models import ChatMessage, ResponsesRequest

_logger = logging.getLogger("codex-chat-bridge")


class SessionRecord:
    """A state snapshot of a single Responses API response.

    messages and tool_context are deep-copied on construction, ensuring
    that subsequent requests for the same response_id cannot mutate
    the persisted history.
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
    """In-memory session store, indexed by response_id."""

    def __init__(self, ttl: int = _DEFAULT_TTL, max_sessions: int = 500) -> None:
        self._ttl = ttl
        self._max_sessions = max_sessions
        self._sessions: dict[str, SessionRecord] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, response_id: str) -> SessionRecord | None:
        """Look up a session; expired entries are treated as missing. Access renews the TTL."""
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
        """Save session state, triggering lazy cleanup.

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
        """Lazy cleanup of expired entries (triggered on each get/save)."""
        now = time.time()
        stale = [rid for rid, rec in self._sessions.items() if now - rec.last_accessed_at > self._ttl]
        for rid in stale:
            del self._sessions[rid]

    def _enforce_cap(self) -> None:
        """Evict the oldest (non-expired) entry when the cap is exceeded."""
        while len(self._sessions) > self._max_sessions:
            oldest = min(self._sessions.items(), key=lambda kv: kv[1].last_accessed_at)[0]
            del self._sessions[oldest]

    @property
    def active_count(self) -> int:
        """Current number of active sessions (for debugging/monitoring)."""
        return len(self._sessions)


# ------------------------------------------------------------------
# Bridge helpers — integrating session with request conversion
# ------------------------------------------------------------------

_global_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    """Global session store singleton."""
    global _global_store
    if _global_store is None:
        _global_store = SessionStore()
    return _global_store


def reset_session_store() -> None:
    """Reset the global session store (for testing)."""
    global _global_store
    _global_store = None


def _assistant_message_from_chat_body(chat_body: dict) -> ChatMessage | None:
    """Extract an assistant message from an upstream Chat Completions response body, for session persistence."""
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
        # has no refusal field.  Preserve refusal as content with a typed
        # prefix so it survives session replay without conflating it with
        # normal assistant text.
        if refusal:
            content = f"[refusal]: {refusal}"
        else:
            content = None
    return ChatMessage(
        role=cast(Literal["system", "user", "assistant", "tool"], role),
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
    """Resolve previous_response_id, returning (existing_messages, tool_context, model) or (None, None, None).

    The returned messages are the session's full saved history (deep-copied, safe to modify).
    tool_context has been merged with the new request's tools.
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
    """Save a session snapshot. If assistant_message is provided, it is appended to messages before persisting.

    SessionRecord deep-copies messages and tool_context on construction,
    so it is safe to modify them before passing them in.
    """
    saved_messages = [*messages, assistant_message] if assistant_message is not None else messages
    store = get_session_store()
    store.save(response_id, SessionRecord(saved_messages, tool_context, model))
