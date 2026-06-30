
"""Tests for the 3 better-than-CodexPlusPlus improvements."""
import unittest

from codex_chat_bridge.models import ResponsesRequest
from codex_chat_bridge.responses_to_chat import responses_to_chat_request
from codex_chat_bridge.chat_to_responses.annotations import message_content_parts


class MessageAnnotationTests(unittest.TestCase):
    """#1: Message-level annotations extracted and merged into output_text parts."""

    def test_string_content_with_message_level_annotations(self):
        msg = {
            "content": "See this",
            "annotations": [
                {"type": "url_citation", "url": "https://example.com", "title": "Example"}
            ],
        }
        parts = message_content_parts(msg)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]["type"], "output_text")
        self.assertEqual(len(parts[0]["annotations"]), 1)
        self.assertEqual(parts[0]["annotations"][0]["type"], "url_citation")

    def test_list_content_merges_message_and_part_annotations(self):
        msg = {
            "content": [
                {"type": "text", "text": "Text A"},
                {"type": "text", "text": "Text B", "annotations": [{"type": "file_citation", "title": "doc"}]},
            ],
            "annotations": [{"type": "url_citation", "url": "https://a.com"}],
        }
        parts = message_content_parts(msg)
        # Part A: only message-level annotations
        self.assertEqual(parts[0]["annotations"], [{"type": "url_citation", "url": "https://a.com"}])
        # Part B: merged — message-level first, then part-level
        self.assertIn({"type": "url_citation", "url": "https://a.com"}, parts[1]["annotations"])
        self.assertIn({"type": "file_citation", "title": "doc"}, parts[1]["annotations"])

    def test_no_annotations_yields_empty_list(self):
        msg = {"content": "plain text"}
        parts = message_content_parts(msg)
        self.assertEqual(parts[0]["annotations"], [])


class EmptyAssistantNormalizationTests(unittest.TestCase):
    """#2: Empty assistant messages get content="" instead of being removed."""

    def test_empty_assistant_preserved_with_empty_content(self):
        from codex_chat_bridge.models import ChatMessage
        messages = [
            ChatMessage(role="user", content="hello"),
            ChatMessage(role="assistant", content=None),
            ChatMessage(role="tool", tool_call_id="call_1", content="result"),
        ]
        result = responses_to_chat_request(
            ResponsesRequest.model_validate({}),
            "fallback-model",
            existing_messages=messages,
        ).messages
        # Assistant should survive with content=""
        assistant_msgs = [m for m in result if m.role == "assistant"]
        self.assertEqual(len(assistant_msgs), 1)
        self.assertEqual(assistant_msgs[0].content, "")

    def test_assistant_with_tool_calls_stays_same(self):
        from codex_chat_bridge.models import ChatMessage
        messages = [
            ChatMessage(role="user", content="hello"),
            ChatMessage(role="assistant", content=None, tool_calls=[{"id": "c1", "function": {"name": "f"}}]),
        ]
        result = responses_to_chat_request(
            ResponsesRequest.model_validate({}),
            "fallback-model",
            existing_messages=messages,
        ).messages
        self.assertEqual(len(result), 2)
        self.assertIsNone(result[1].content)  # Keep original None when tool_calls exist


class OrphanToolOutputTests(unittest.TestCase):
    """#3: Orphan tool outputs downgrade to user message."""

    def test_orphan_function_call_output_becomes_user_message(self):
        payload = ResponsesRequest.model_validate({
            "input": [{"type": "function_call_output", "call_id": "call_orphan", "output": {"status": "ok"}}]
        })
        req = responses_to_chat_request(payload, "fallback-model")
        # No matching function_call → should become user message
        tool_msgs = [m for m in req.messages if m.role == "tool"]
        user_msgs = [m for m in req.messages if "call_orphan" in str(m.content)]
        self.assertEqual(len(tool_msgs), 0)
        self.assertEqual(len(user_msgs), 1)
        self.assertIn("Function call output", user_msgs[0].content)

    def test_matched_function_call_output_stays_tool_message(self):
        payload = ResponsesRequest.model_validate({
            "input": [
                {"type": "function_call", "call_id": "call_match", "name": "get", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "call_match", "output": "done"},
            ]
        })
        req = responses_to_chat_request(payload, "fallback-model")
        tool_msgs = [m for m in req.messages if m.role == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertEqual(tool_msgs[0].tool_call_id, "call_match")


if __name__ == "__main__":
    unittest.main()
