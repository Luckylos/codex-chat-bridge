"""Session store — stateful context management for previous_response_id.

Persists messages + tool_context for later request recovery, enabling
Responses API session continuity.  Currently a single-process in-memory
store with lazy TTL cleanup.  For multi-process/persistent backends,
replace the _sessions backend.

Reasoning cache
~~~~~~~~~~~~~~~
When a reasoning model (DeepSeek-R1, etc.) produces thinking followed by
a tool call, the reasoning_content is saved in ``SessionRecord.reasoning_cache``
keyed by ``tool_call_id``.  On the next turn, ``resolve_session`` calls
``apply_reasoning_cache`` which restores the cached reasoning into assistant
messages that have tool_calls but are missing their reasoning_content.  This
prevents the model from "forgetting" its prior thinking across tool-call turns.

Without the cache, ``ensure_tool_call_reasoning_content`` backfills an empty
string, which avoids upstream 400 errors but causes model quality degradation
because the model cannot reference its own prior reasoning.
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

    reasoning_cache maps tool_call_id → reasoning text so that the next
    request in the same session can restore prior thinking for reasoning
    models that lose context across tool-call turns.
    """

    __slots__ = (
        "messages",
        "tool_context",
        "model",
        "created_at",
        "last_accessed_at",
        "reasoning_cache",
    )

    def __init__(
        self,
        messages: list[ChatMessage],
        tool_context: BridgeToolContext,
        model: str,
        created_at: float | None = None,
        reasoning_cache: dict[str, str] | None = None,
    ) -> None:
        self.messages: list[ChatMessage] = copy.deepcopy(messages)
        self.tool_context: BridgeToolContext = copy.deepcopy(tool_context)
        self.model = model
        self.created_at = created_at or time.time()
        self.last_accessed_at = self.created_at
        self.reasoning_cache: dict[str, str] = dict(reasoning_cache) if reasoning_cache else {}


# ---------------------------------------------------------------------------
# Reasoning cache helpers
# ---------------------------------------------------------------------------


def extract_reasoning_cache(messages: list[ChatMessage]) -> dict[str, str]:
    """Extract tool_call_id → reasoning_content mappings from assistant messages.

    Only entries where the assistant message has *both* tool_calls and
    non-empty reasoning_content are cached.  This preserves the model's
    thinking so it can be replayed on subsequent turns.
    """
    cache: dict[str, str] = {}
    for msg in messages:
        if msg.role != "assistant" or not msg.tool_calls:
            continue
        reasoning = msg.reasoning_content
        if not reasoning or not reasoning.strip():
            continue
        for tc in msg.tool_calls:
            tc_id = tc.get("id") or tc.get("call_id") or ""
            if isinstance(tc_id, str) and tc_id:
                cache[tc_id] = reasoning
    return cache


def apply_reasoning_cache(messages: list[ChatMessage], cache: dict[str, str]) -> None:
    """Restore cached reasoning into assistant messages missing reasoning_content.

    For each assistant message that has tool_calls but empty/missing
    reasoning_content, look up each of its tool_call IDs in *cache*.
    If a match is found, restore the cached reasoning text.

    This is called by ``resolve_session`` before returning messages so
    that downstream conversion (``ensure_tool_call_reasoning_content``)
    finds real reasoning instead of having to backfill empty strings.
    """
    if not cache:
        return
    for msg in messages:
        if msg.role != "assistant" or not msg.tool_calls:
            continue
        if msg.reasoning_content and msg.reasoning_content.strip():
            continue
        for tc in msg.tool_calls:
            tc_id = tc.get("id") or tc.get("call_id") or ""
            if isinstance(tc_id, str) and tc_id:
                cached = cache.get(tc_id)
                if cached:
                    msg.reasoning_content = cached
                    break


# ---------------------------------------------------------------------------
# Session store
# ---------------------------------------------------------------------------

_DEFAULT_TTL = 3600  # 1 hour


class SessionStore:
    """In-memory session store, indexed by response_id."""

    def __init__(self, ttl: int = _DEFAULT_TTL, max_sessions: int = 500) -> None:
        self._ttl = ttl
        self._max_sessions = max_sessions
        self._sessions: dict[str, SessionRecord] = {}

    def get(self, response_id: str) -> SessionRecord | None:
        """Look up a session; expired entries are treated as missing. Access renews the TTL."""
        record = self._sessions.get(response_id)
        if record is None:
            return None
        if time.time() - record.last_accessed_at > self._ttl:
            del self._sessions[response_id]
            return None
        record.last_accessed_at = time.time()
        returned = SessionRecord(
            record.messages,
            record.tool_context,
            record.model,
            created_at=record.created_at,
            reasoning_cache=record.reasoning_cache,
        )
        returned.last_accessed_at = record.last_accessed_at
        return returned

    def save(self, response_id: str, record: SessionRecord) -> None:
        """Save session state, triggering lazy cleanup.

        Constructs a new SessionRecord (which deep-copies) so the stored
        data is fully isolated from the caller's references.
        """
        now = time.time()
        new_record = SessionRecord(
            record.messages,
            record.tool_context,
            record.model,
            created_at=now,
            reasoning_cache=record.reasoning_cache,
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
    """Merge tools from a new request into an existing session's tool context."""
    merged = copy.deepcopy(existing)
    merged.merge(build_tool_context_from_request(payload))
    return merged


def resolve_session(
    payload: ResponsesRequest,
) -> tuple[list[ChatMessage] | None, BridgeToolContext | None, str | None]:
    """Resolve previous_response_id, returning (existing_messages, tool_context, model) or (None, None, None).

    The returned messages are the session's full saved history (deep-copied, safe to modify).
    tool_context has been merged with the new request's tools.

    Before returning, ``apply_reasoning_cache`` restores cached reasoning
    into assistant messages that would otherwise lose their thinking across
    tool-call turns.
    """
    prev_id = getattr(payload, "previous_response_id", None)
    if not prev_id:
        return None, None, None

    store = get_session_store()
    record = store.get(prev_id)
    if record is None:
        return None, None, None

    # Restore cached reasoning before returning messages to the caller.
    apply_reasoning_cache(record.messages, record.reasoning_cache)

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

    After persisting messages, the reasoning cache is extracted from all
    messages (including the appended assistant_message) and stored so
    that subsequent ``resolve_session`` can restore prior thinking.
    """
    saved_messages = [*messages, assistant_message] if assistant_message is not None else messages
    cache = extract_reasoning_cache(saved_messages)
    store = get_session_store()
    store.save(response_id, SessionRecord(
        saved_messages, tool_context, model, reasoning_cache=cache,
    ))
