from __future__ import annotations

import unittest

from codex_chat_bridge.transform_chat_to_responses import chat_text_to_responses


class ResponseSemanticsTests(unittest.TestCase):
    def test_nonstream_length_finish_reason_maps_to_incomplete_with_usage(self) -> None:
        chat_body = {
            "id": "chatcmpl_incomplete",
            "object": "chat.completion",
            "created": 123,
            "model": "demo-model",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "partial answer"},
                    "finish_reason": "length",
                }
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        }

        response = chat_text_to_responses(chat_body, "fallback-model")

        self.assertEqual(response.status, "incomplete")
        self.assertEqual(response.created_at, 123)
        self.assertEqual(response.output_text, "partial answer")
        self.assertEqual(response.incomplete_details, {"reason": "max_output_tokens"})
        self.assertEqual(response.usage["input_tokens"], 11)
        self.assertEqual(response.usage["output_tokens"], 7)
        self.assertEqual(response.usage["total_tokens"], 18)

    def test_nonstream_refusal_parts_are_restored_without_output_text(self) -> None:
        chat_body = {
            "id": "chatcmpl_refusal",
            "object": "chat.completion",
            "created": 456,
            "model": "demo-model",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "refusal", "refusal": "I can’t help with that."},
                        ],
                        "refusal": "Policy refusal.",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }

        response = chat_text_to_responses(chat_body, "fallback-model")

        self.assertEqual(response.status, "completed")
        self.assertEqual(response.output_text, "")
        self.assertEqual(len(response.output), 1)
        message_item = response.output[0]
        self.assertEqual(message_item["type"], "message")
        self.assertEqual(message_item["content"][0], {"type": "refusal", "refusal": "I can’t help with that."})
        self.assertEqual(message_item["content"][1], {"type": "refusal", "refusal": "Policy refusal."})


if __name__ == "__main__":
    unittest.main()
