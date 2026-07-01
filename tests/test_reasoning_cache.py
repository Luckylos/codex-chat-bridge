"""Tests for reasoning cache — extract, apply, and session integration.

Covers the core scenario: a reasoning model produces thinking followed by
a tool call, the thinking is cached on save, and restored on resolve so
that the next turn's request conversion sees real reasoning instead of
empty-string backfills.
"""

from __future__ import annotations

import unittest

from codex_chat_bridge.models import ChatMessage, ResponsesRequest
from codex_chat_bridge.bridge_context import BridgeToolContext
from codex_chat_bridge.protocol.session import (
    SessionRecord,
    SessionStore,
    extract_reasoning_cache,
    apply_reasoning_cache,
    resolve_session,
    save_session,
    get_session_store,
    reset_session_store,
)


class ExtractReasoningCacheTests(unittest.TestCase):
    """Tests for extract_reasoning_cache()."""

    def test_extracts_from_assistant_with_tool_calls_and_reasoning(self) -> None:
        messages = [
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}],
                reasoning_content="Let me analyze this step by step...",
            ),
        ]
        cache = extract_reasoning_cache(messages)
        self.assertEqual(cache, {"call_1": "Let me analyze this step by step..."})

    def test_multiple_tool_calls_in_one_message(self) -> None:
        messages = [
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    {"id": "call_a", "type": "function", "function": {"name": "read", "arguments": "{}"}},
                    {"id": "call_b", "type": "function", "function": {"name": "write", "arguments": "{}"}},
                ],
                reasoning_content="I need both files",
            ),
        ]
        cache = extract_reasoning_cache(messages)
        self.assertEqual(cache, {"call_a": "I need both files", "call_b": "I need both files"})

    def test_skips_messages_without_tool_calls(self) -> None:
        messages = [
            ChatMessage(role="assistant", content="Hello", reasoning_content="thinking"),
        ]
        cache = extract_reasoning_cache(messages)
        self.assertEqual(cache, {})

    def test_skips_messages_without_reasoning(self) -> None:
        messages = [
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
                reasoning_content=None,
            ),
        ]
        cache = extract_reasoning_cache(messages)
        self.assertEqual(cache, {})

    def test_skips_empty_reasoning(self) -> None:
        messages = [
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
                reasoning_content="",
            ),
        ]
        cache = extract_reasoning_cache(messages)
        self.assertEqual(cache, {})

    def test_skips_non_assistant_roles(self) -> None:
        messages = [
            ChatMessage(role="tool", content="result", tool_call_id="call_1", reasoning_content="thinking"),
        ]
        cache = extract_reasoning_cache(messages)
        self.assertEqual(cache, {})

    def test_uses_call_id_fallback(self) -> None:
        messages = [
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[{"call_id": "alt_id", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
                reasoning_content="deep thoughts",
            ),
        ]
        cache = extract_reasoning_cache(messages)
        self.assertEqual(cache, {"alt_id": "deep thoughts"})


class ApplyReasoningCacheTests(unittest.TestCase):
    """Tests for apply_reasoning_cache()."""

    def test_restores_reasoning_from_cache(self) -> None:
        messages = [
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
                reasoning_content=None,
            ),
        ]
        apply_reasoning_cache(messages, {"call_1": "I was thinking deeply"})
        self.assertEqual(messages[0].reasoning_content, "I was thinking deeply")

    def test_does_not_overwrite_existing_reasoning(self) -> None:
        messages = [
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
                reasoning_content="original reasoning",
            ),
        ]
        apply_reasoning_cache(messages, {"call_1": "cached reasoning"})
        self.assertEqual(messages[0].reasoning_content, "original reasoning")

    def test_does_not_overwrite_whitespace_only_reasoning(self) -> None:
        """Whitespace-only reasoning_content is treated as empty and gets backfilled."""
        messages = [
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
                reasoning_content="   ",
            ),
        ]
        apply_reasoning_cache(messages, {"call_1": "cached reasoning"})
        self.assertEqual(messages[0].reasoning_content, "cached reasoning")

    def test_skips_non_matching_call_id(self) -> None:
        messages = [
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
                reasoning_content=None,
            ),
        ]
        apply_reasoning_cache(messages, {"call_999": "other reasoning"})
        self.assertIsNone(messages[0].reasoning_content)

    def test_empty_cache_is_noop(self) -> None:
        messages = [
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
                reasoning_content=None,
            ),
        ]
        apply_reasoning_cache(messages, {})
        self.assertIsNone(messages[0].reasoning_content)

    def test_restores_first_matching_call_id(self) -> None:
        """When multiple tool_calls exist, the first matching call_id wins."""
        messages = [
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    {"id": "call_a", "type": "function", "function": {"name": "fa", "arguments": "{}"}},
                    {"id": "call_b", "type": "function", "function": {"name": "fb", "arguments": "{}"}},
                ],
                reasoning_content=None,
            ),
        ]
        apply_reasoning_cache(messages, {"call_b": "reasoning B", "call_a": "reasoning A"})
        # First matching (call_a) wins because iteration stops at break
        self.assertEqual(messages[0].reasoning_content, "reasoning A")


class ReasoningCacheSessionIntegrationTests(unittest.TestCase):
    """Integration: reasoning_cache flows through save → resolve."""

    def setUp(self) -> None:
        reset_session_store()

    def tearDown(self) -> None:
        reset_session_store()

    def test_cache_survives_save_and_resolve(self) -> None:
        """Full lifecycle: assistant with reasoning+tool_calls → save → resolve → reasoning restored."""
        store = get_session_store()

        # Turn 1: model thinks, then calls a tool
        messages_turn1 = [
            ChatMessage(role="user", content="Analyze this"),
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[{"id": "call_step1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}],
                reasoning_content="Step 1: I need to read the file first",
            ),
            ChatMessage(role="tool", content="file contents", tool_call_id="call_step1"),
        ]
        save_session("resp_turn1", messages_turn1, BridgeToolContext(), "reasoning-model")

        # Turn 2: client sends previous_response_id — resolve should restore reasoning
        payload = ResponsesRequest.model_validate({
            "model": "reasoning-model",
            "previous_response_id": "resp_turn1",
            "input": "Now summarize it",
        })
        msgs, ctx, model = resolve_session(payload)
        assert msgs is not None
        # The assistant message should have its reasoning restored
        assistant_msgs = [m for m in msgs if m.role == "assistant"]
        self.assertEqual(len(assistant_msgs), 1)
        self.assertEqual(assistant_msgs[0].reasoning_content, "Step 1: I need to read the file first")

    def test_cache_isolation_across_sessions(self) -> None:
        """Two independent sessions should not cross-contaminate reasoning caches."""
        reset_session_store()

        messages_a = [
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[{"id": "call_a", "type": "function", "function": {"name": "fa", "arguments": "{}"}}],
                reasoning_content="thinking for session A",
            ),
        ]
        messages_b = [
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[{"id": "call_b", "type": "function", "function": {"name": "fb", "arguments": "{}"}}],
                reasoning_content="thinking for session B",
            ),
        ]
        save_session("resp_a", messages_a, BridgeToolContext(), "m")
        save_session("resp_b", messages_b, BridgeToolContext(), "m")

        # Resolve session A — must not get B's reasoning
        payload_a = ResponsesRequest.model_validate({
            "model": "m",
            "previous_response_id": "resp_a",
            "input": "continue A",
        })
        msgs_a, _, _ = resolve_session(payload_a)
        assert msgs_a is not None
        assistants_a = [m for m in msgs_a if m.role == "assistant"]
        self.assertEqual(assistants_a[0].reasoning_content, "thinking for session A")

    def test_cache_keyed_by_tool_call_id(self) -> None:
        """Multiple assistant messages with different call_ids each get their own reasoning."""
        reset_session_store()

        messages = [
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "f1", "arguments": "{}"}}],
                reasoning_content="first thinking",
            ),
            ChatMessage(role="tool", content="r1", tool_call_id="call_1"),
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[{"id": "call_2", "type": "function", "function": {"name": "f2", "arguments": "{}"}}],
                reasoning_content="second thinking",
            ),
        ]
        save_session("resp_multi", messages, BridgeToolContext(), "m")

        # Verify cache has both entries
        record = get_session_store().get("resp_multi")
        assert record is not None
        self.assertEqual(record.reasoning_cache["call_1"], "first thinking")
        self.assertEqual(record.reasoning_cache["call_2"], "second thinking")

        # Resolve — both assistant messages should have their respective reasoning
        payload = ResponsesRequest.model_validate({
            "model": "m",
            "previous_response_id": "resp_multi",
            "input": "continue",
        })
        msgs, _, _ = resolve_session(payload)
        assert msgs is not None
        assistants = [m for m in msgs if m.role == "assistant"]
        self.assertEqual(len(assistants), 2)
        self.assertEqual(assistants[0].reasoning_content, "first thinking")
        self.assertEqual(assistants[1].reasoning_content, "second thinking")


class SessionRecordReasoningCacheTests(unittest.TestCase):
    """Tests for SessionRecord.reasoning_cache isolation."""

    def test_reasoning_cache_is_deep_copied(self) -> None:
        original_cache = {"call_1": "thinking"}
        record = SessionRecord(
            messages=[ChatMessage(role="user", content="hi")],
            tool_context=BridgeToolContext(),
            model="m",
            reasoning_cache=original_cache,
        )
        # Mutate the original — should not affect the record
        original_cache["call_2"] = "more thinking"
        self.assertNotIn("call_2", record.reasoning_cache)

    def test_default_empty_cache(self) -> None:
        record = SessionRecord(
            messages=[ChatMessage(role="user", content="hi")],
            tool_context=BridgeToolContext(),
            model="m",
        )
        self.assertEqual(record.reasoning_cache, {})
