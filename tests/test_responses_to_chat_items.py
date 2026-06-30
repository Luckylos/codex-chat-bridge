from __future__ import annotations

import unittest

from codex_chat_bridge.bridge_context import BridgeToolContext
from codex_chat_bridge.models import ResponsesRequest
from codex_chat_bridge.responses_to_chat.items import append_input_items_as_chat_messages


class ResponsesToChatItemsLoggingTests(unittest.TestCase):
    def test_unknown_item_type_logs_and_is_skipped(self) -> None:
        payload = ResponsesRequest.model_validate(
            {
                "model": "test-model",
                "input": [{"type": "mystery_item", "value": 1}],
            }
        )
        messages = []

        with self.assertLogs("codex-chat-bridge", level="DEBUG") as logs:
            append_input_items_as_chat_messages(payload, messages, BridgeToolContext())

        self.assertEqual(messages, [])
        self.assertTrue(any("mystery_item" in entry for entry in logs.output))
