from __future__ import annotations

import time
import unittest

from codex_chat_bridge.models import ChatMessage, ResponsesRequest
from codex_chat_bridge.bridge_context import BridgeToolContext
from codex_chat_bridge.protocol.session import (
    SessionRecord,
    SessionStore,
    resolve_session,
    save_session,
    get_session_store,
)


class SessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = SessionStore(ttl=3600)

    def test_save_and_get(self) -> None:
        record = SessionRecord(
            messages=[ChatMessage(role="user", content="hello")],
            tool_context=BridgeToolContext(),
            model="test-model",
        )
        self.store.save("resp_abc", record)

        got = self.store.get("resp_abc")
        assert got is not None
        self.assertEqual(len(got.messages), 1)
        self.assertEqual(got.messages[0].content, "hello")
        self.assertEqual(got.model, "test-model")

    def test_get_returns_deep_copy(self) -> None:
        ctx = BridgeToolContext()
        ctx.add_tool_search_tool()
        self.store.save(
            "resp_copy",
            SessionRecord(
                messages=[ChatMessage(role="user", content="hello")],
                tool_context=ctx,
                model="test-model",
            ),
        )

        got = self.store.get("resp_copy")
        assert got is not None
        got.messages.append(ChatMessage(role="user", content="mutated"))
        got.tool_context.tool_search_enabled = False

        fresh = self.store.get("resp_copy")
        assert fresh is not None
        self.assertEqual(len(fresh.messages), 1)
        self.assertTrue(fresh.tool_context.tool_search_enabled)

    def test_get_nonexistent_returns_none(self) -> None:
        self.assertIsNone(self.store.get("resp_nope"))

    def test_get_expired_returns_none(self) -> None:
        record = SessionRecord(
            messages=[ChatMessage(role="user", content="hi")],
            tool_context=BridgeToolContext(),
            model="m",
        )
        # 先 save（此时 created_at = time.time()）
        self.store.save("resp_expired", record)
        # 直接修改内部记录的 created_at（越过 save 的时间覆盖）
        stored = self.store._sessions["resp_expired"]
        stored.created_at = time.time() - 7200
        self.assertIsNone(self.store.get("resp_expired"))

    def test_lazy_cleanup_only_expired(self) -> None:
        fresh_record = SessionRecord(
            messages=[ChatMessage(role="user", content="fresh")],
            tool_context=BridgeToolContext(),
            model="m",
        )
        stale_record = SessionRecord(
            messages=[ChatMessage(role="user", content="stale")],
            tool_context=BridgeToolContext(),
            model="m",
        )
        self.store.save("resp_fresh", fresh_record)
        self.store.save("resp_stale", stale_record)
        # 手动过期 stale，fresh 保持正常
        self.store._sessions["resp_stale"].created_at = time.time() - 7200
        # get fresh 触发 cleanup
        self.assertIsNotNone(self.store.get("resp_fresh"))
        self.assertIsNone(self.store.get("resp_stale"))

    def test_active_count(self) -> None:
        self.assertEqual(self.store.active_count, 0)
        record = SessionRecord(
            messages=[ChatMessage(role="user", content="x")],
            tool_context=BridgeToolContext(),
            model="m",
        )
        self.store.save("resp_1", record)
        self.assertEqual(self.store.active_count, 1)

    def test_resolve_session_no_prev_id(self) -> None:
        payload = ResponsesRequest.model_validate({"model": "m", "input": "hello"})
        msgs, ctx, model = resolve_session(payload)
        self.assertIsNone(msgs)
        self.assertIsNone(ctx)
        self.assertIsNone(model)

    def test_resolve_session_with_valid_prev_id(self) -> None:
        store = get_session_store()
        record = SessionRecord(
            messages=[ChatMessage(role="user", content="previous")],
            tool_context=BridgeToolContext(),
            model="prev-model",
        )
        store.save("resp_prev", record)

        payload = ResponsesRequest.model_validate({
            "model": "m",
            "previous_response_id": "resp_prev",
            "input": "continue",
        })
        msgs, ctx, model = resolve_session(payload)
        assert msgs is not None
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].content, "previous")
        self.assertEqual(model, "prev-model")

    def test_save_session_integration(self) -> None:
        store = get_session_store()
        # 清理旧状态
        save_session(
            "resp_new",
            [ChatMessage(role="user", content="saved")],
            BridgeToolContext(),
            "saved-model",
        )
        record = store.get("resp_new")
        assert record is not None
        self.assertEqual(record.messages[0].content, "saved")
        self.assertEqual(record.model, "saved-model")


class SessionImmutabilityTests(unittest.TestCase):
    """Verify that session records are isolated from caller mutations."""

    def test_messages_deep_copy_on_save(self) -> None:
        """Modifying the original messages list after save does not
        affect the persisted session."""
        messages = [ChatMessage(role="user", content="original")]
        save_session("resp_imm1", messages, BridgeToolContext(), "m")
        # Mutate the original
        messages.append(ChatMessage(role="user", content="mutated"))
        record = get_session_store().get("resp_imm1")
        assert record is not None
        self.assertEqual(len(record.messages), 1)
        self.assertEqual(record.messages[0].content, "original")

    def test_tool_context_deep_copy_on_save(self) -> None:
        """Modifying the original tool_context after save does not
        affect the persisted session."""
        ctx = BridgeToolContext()
        save_session("resp_imm2", [], ctx, "m")
        # Mutate the original
        ctx.tool_search_enabled = True
        record = get_session_store().get("resp_imm2")
        assert record is not None
        self.assertFalse(record.tool_context.tool_search_enabled)


class SessionToolMergeTests(unittest.TestCase):
    """Verify that resolve_session merges new request tools into session context."""

    def test_new_tool_added_to_session_context(self) -> None:
        store = get_session_store()
        # Store a session with one tool
        ctx = BridgeToolContext()
        ctx.add_function_tool({"type": "function", "function": {"name": "old_tool", "parameters": {}}})
        store.save("resp_merge1", SessionRecord(
            messages=[ChatMessage(role="user", content="hi")],
            tool_context=ctx,
            model="m",
        ))
        # Resolve with a new request that adds another tool
        payload = ResponsesRequest.model_validate({
            "model": "m",
            "previous_response_id": "resp_merge1",
            "input": "continue",
            "tools": [{"type": "function", "function": {"name": "new_tool", "parameters": {}}}],
        })
        msgs, merged_ctx, model = resolve_session(payload)
        assert merged_ctx is not None
        # Both old and new tools should be present
        self.assertTrue(merged_ctx.tool_search_enabled is False)
        old_spec = merged_ctx.lookup_chat_name("old_tool")
        new_spec = merged_ctx.lookup_chat_name("new_tool")
        self.assertIsNotNone(old_spec)
        self.assertIsNotNone(new_spec)

    def test_resolve_session_keeps_persisted_context_isolated(self) -> None:
        store = get_session_store()
        ctx = BridgeToolContext()
        ctx.add_function_tool({"type": "function", "function": {"name": "old_tool", "parameters": {}}})
        store.save(
            "resp_merge_isolated",
            SessionRecord(
                messages=[ChatMessage(role="user", content="hi")],
                tool_context=ctx,
                model="m",
            ),
        )

        payload = ResponsesRequest.model_validate({
            "model": "m",
            "previous_response_id": "resp_merge_isolated",
            "input": "continue",
            "tools": [{"type": "function", "function": {"name": "new_tool", "parameters": {}}}],
        })
        _msgs, merged_ctx, _model = resolve_session(payload)
        assert merged_ctx is not None
        self.assertIsNotNone(merged_ctx.lookup_chat_name("new_tool"))

        merged_ctx.add_tool_search_tool()

        stored = store.get("resp_merge_isolated")
        assert stored is not None
        self.assertIsNone(stored.tool_context.lookup_chat_name("new_tool"))
        self.assertFalse(stored.tool_context.tool_search_enabled)
