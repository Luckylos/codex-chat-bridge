from __future__ import annotations

import unittest
import os

from codex_chat_bridge.models import ChatMessage, ResponsesRequest
from codex_chat_bridge.transform_responses_to_chat import responses_to_chat_request


class MultiTurnToolRoundTripTests(unittest.TestCase):
    def test_typed_function_call_output_items_build_expected_chat_sequence(self) -> None:
        payload = ResponsesRequest.model_validate(
            {
                "model": "demo-model",
                "input": [
                    {"role": "user", "content": [{"type": "input_text", "text": "weather?"}]},
                    {"type": "function_call", "call_id": "call_1", "name": "get_weather", "arguments": {"city": "Tokyo"}},
                    {"type": "function_call_output", "call_id": "call_1", "output": {"temp": 23, "unit": "C"}},
                    {"role": "user", "content": [{"type": "input_text", "text": "summarize it"}]},
                ],
            }
        )

        request = responses_to_chat_request(payload, "fallback-model")
        messages = [message.model_dump(exclude_none=True) for message in request.messages]

        self.assertEqual(
            messages,
            [
                {"role": "user", "content": "weather?"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "arguments": '{"city":"Tokyo"}',
                            },
                        }
                    ],
                    "reasoning_content": "tool call",
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "content": '{"temp":23,"unit":"C"}',
                },
                {"role": "user", "content": "summarize it"},
            ],
        )

    def test_generic_tool_message_preserves_tool_call_id(self) -> None:
        payload = ResponsesRequest.model_validate(
            {
                "input": [
                    {
                        "role": "tool",
                        "tool_call_id": "call_9",
                        "content": [{"type": "output_text", "text": "tool says ok"}],
                    }
                ]
            }
        )

        request = responses_to_chat_request(payload, "fallback-model")
        self.assertEqual(request.messages[0].role, "tool")
        self.assertEqual(request.messages[0].tool_call_id, "call_9")
        self.assertEqual(request.messages[0].content, "tool says ok")

    def test_generic_assistant_message_preserves_tool_calls_and_reasoning(self) -> None:
        payload = ResponsesRequest.model_validate(
            {
                "input": [
                    {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "Need weather first.",
                        "tool_calls": [
                            {
                                "id": "call_7",
                                "function": {
                                    "name": "get_weather",
                                    "arguments": {"city": "Osaka"},
                                },
                            }
                        ],
                    }
                ]
            }
        )

        request = responses_to_chat_request(payload, "fallback-model")
        message = request.messages[0].model_dump(exclude_none=True)

        self.assertEqual(message["role"], "assistant")
        self.assertEqual(message["reasoning_content"], "Need weather first.")
        self.assertEqual(
            message["tool_calls"],
            [
                {
                    "id": "call_7",
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "arguments": '{"city":"Osaka"}',
                    },
                }
            ],
        )

    def test_generic_assistant_message_with_tool_calls_backfills_placeholder_reasoning(self) -> None:
        payload = ResponsesRequest.model_validate(
            {
                "input": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_8",
                                "function": {
                                    "name": "get_weather",
                                    "arguments": {"city": "Kyoto"},
                                },
                            }
                        ],
                    }
                ]
            }
        )

        request = responses_to_chat_request(payload, "fallback-model")
        message = request.messages[0].model_dump(exclude_none=True)
        self.assertEqual(message["reasoning_content"], "tool call")

    def test_system_and_developer_messages_collapse_to_head_system_message(self) -> None:
        payload = ResponsesRequest.model_validate(
            {
                "instructions": "global system",
                "input": [
                    {"role": "user", "content": "u1"},
                    {"role": "developer", "content": [{"type": "input_text", "text": "dev note"}]},
                    {"role": "system", "content": "late system"},
                    {"role": "user", "content": "u2"},
                ],
            }
        )

        request = responses_to_chat_request(payload, "fallback-model")
        messages = [message.model_dump(exclude_none=True) for message in request.messages]
        self.assertEqual(
            messages,
            [
                {"role": "system", "content": "global system\n\ndev note\n\nlate system"},
                {"role": "user", "content": "u1"},
                {"role": "user", "content": "u2"},
            ],
        )

    def test_instruction_array_flattens_to_system_text(self) -> None:
        payload = ResponsesRequest.model_validate(
            {
                "instructions": [
                    {"type": "input_text", "text": "sys1"},
                    {"type": "input_text", "text": "sys2"},
                ],
                "input": "hello",
            }
        )
        request = responses_to_chat_request(payload, "fallback-model")
        self.assertEqual(request.messages[0].role, "system")
        self.assertEqual(request.messages[0].content, "sys1\n\nsys2")

    def test_refusal_content_part_maps_to_chat_text_when_request_side_replayed(self) -> None:
        payload = ResponsesRequest.model_validate(
            {
                "input": [
                    {
                        "role": "assistant",
                        "content": [{"type": "refusal", "refusal": "Cannot comply."}],
                    }
                ]
            }
        )
        request = responses_to_chat_request(payload, "fallback-model")
        self.assertEqual(request.messages[0].content, "Cannot comply.")

    def test_o_series_uses_max_completion_tokens_instead_of_max_tokens(self) -> None:
        payload = ResponsesRequest.model_validate(
            {
                "model": "o3-mini",
                "input": "hello",
                "max_output_tokens": 77,
            }
        )
        request = responses_to_chat_request(payload, "fallback-model")
        self.assertIsNone(request.max_tokens)
        self.assertEqual(request.max_completion_tokens, 77)

    def test_trailing_reasoning_item_backfills_previous_assistant_tool_call_message(self) -> None:
        payload = ResponsesRequest.model_validate(
            {
                "input": [
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_10",
                                "function": {
                                    "name": "get_weather",
                                    "arguments": {"city": "Seoul"},
                                },
                            }
                        ],
                    },
                    {
                        "type": "reasoning",
                        "summary": [{"type": "summary_text", "text": "Need weather first."}],
                    },
                ]
            }
        )

        request = responses_to_chat_request(payload, "fallback-model")
        message = request.messages[0].model_dump(exclude_none=True)
        self.assertEqual(message["reasoning_content"], "tool call\n\nNeed weather first.")

    def test_function_call_output_text_parts_are_flattened_before_upstream(self) -> None:
        payload = ResponsesRequest.model_validate(
            {
                "input": [
                    {
                        "type": "function_call_output",
                        "call_id": "call_4",
                        "output": [
                            {"type": "output_text", "text": "sunny"},
                            {"type": "output_text", "text": "23C"},
                        ],
                    }
                ]
            }
        )

        request = responses_to_chat_request(payload, "fallback-model")
        self.assertEqual(request.messages[0].content, "sunny\n23C")

    def test_plain_string_input_becomes_user_message(self) -> None:
        payload = ResponsesRequest.model_validate({"input": "hello"})

        request = responses_to_chat_request(payload, "fallback-model")
        self.assertEqual(len(request.messages), 1)
        self.assertEqual(request.messages[0].role, "user")
        self.assertEqual(request.messages[0].content, "hello")

    def test_typed_input_text_item_becomes_user_message(self) -> None:
        payload = ResponsesRequest.model_validate({"input": [{"type": "input_text", "text": "ping"}]})

        request = responses_to_chat_request(payload, "fallback-model")
        self.assertEqual(len(request.messages), 1)
        self.assertEqual(request.messages[0].role, "user")
        self.assertEqual(request.messages[0].content, "ping")

    def test_top_level_input_image_becomes_user_multimodal_message(self) -> None:
        payload = ResponsesRequest.model_validate(
            {"input": [{"type": "input_image", "image_url": "data:image/png;base64,AAAA", "detail": "low"}]}
        )

        request = responses_to_chat_request(payload, "fallback-model")
        self.assertEqual(request.messages[0].role, "user")
        self.assertEqual(
            request.messages[0].content,
            [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA", "detail": "low"}}],
        )

    def test_user_message_text_and_image_content_becomes_chat_multimodal_parts(self) -> None:
        payload = ResponsesRequest.model_validate(
            {
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "describe this"},
                            {"type": "input_image", "image_url": "https://example.com/a.png"},
                        ],
                    }
                ]
            }
        )

        request = responses_to_chat_request(payload, "fallback-model")
        self.assertEqual(request.messages[0].role, "user")
        self.assertEqual(
            request.messages[0].content,
            [
                {"type": "text", "text": "describe this"},
                {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
            ],
        )

    def test_unsupported_top_level_item_is_ignored_like_cc_switch(self) -> None:
        payload = ResponsesRequest.model_validate({"input": [{"type": "input_audio", "audio_url": "https://example.com/a.mp3"}]})

        request = responses_to_chat_request(payload, "fallback-model")
        self.assertEqual(request.messages, [])

    def test_unknown_message_content_parts_are_ignored_while_text_survives(self) -> None:
        payload = ResponsesRequest.model_validate(
            {
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "ping"},
                            {"type": "input_audio", "audio_url": "https://example.com/a.mp3"},
                            123,
                        ],
                    }
                ]
            }
        )

        request = responses_to_chat_request(payload, "fallback-model")
        self.assertEqual(request.messages[0].content, "ping")

    def test_reasoning_effort_max_is_remapped_to_xhigh(self) -> None:
        payload = ResponsesRequest.model_validate(
            {
                "reasoning": {"effort": "max"},
                "input": "hello",
            }
        )

        request = responses_to_chat_request(payload, "fallback-model")
        self.assertEqual(request.reasoning_effort, "xhigh")
        self.assertIsNone(request.thinking)

    def test_text_format_json_schema_maps_to_chat_response_format(self) -> None:
        payload = ResponsesRequest.model_validate(
            {
                "input": "hello",
                "text": {
                    "format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "answer_schema",
                            "schema": {
                                "type": "object",
                                "properties": {"answer": {"type": "string"}},
                                "required": ["answer"],
                                "additionalProperties": False,
                            },
                            "strict": True,
                        },
                    }
                },
            }
        )

        request = responses_to_chat_request(payload, "fallback-model")
        self.assertEqual(
            request.response_format,
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "answer_schema",
                    "schema": {
                        "type": "object",
                        "properties": {"answer": {"type": "string"}},
                        "required": ["answer"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                },
            },
        )

    def test_top_level_response_format_maps_to_chat_response_format(self) -> None:
        payload = ResponsesRequest.model_validate(
            {
                "input": "hello",
                "response_format": {"type": "json_object"},
            }
        )

        request = responses_to_chat_request(payload, "fallback-model")
        self.assertEqual(request.response_format, {"type": "json_object"})

    def test_cc_switch_style_extra_root_fields_passthrough(self) -> None:
        payload = ResponsesRequest.model_validate(
            {
                "input": "hello",
                "metadata": {"trace_id": "abc"},
                "n": 2,
                "parallel_tool_calls": True,
                "presence_penalty": 0.3,
                "frequency_penalty": 0.2,
                "seed": 42,
                "service_tier": "default",
                "stop": ["DONE"],
                "user": "tester",
                "logit_bias": {"42": -100},
                "logprobs": True,
                "top_logprobs": 3,
                "stream_options": {"include_usage": False},
            }
        )

        request = responses_to_chat_request(payload, "fallback-model")
        self.assertEqual(request.metadata, {"trace_id": "abc"})
        self.assertEqual(request.n, 2)
        self.assertTrue(request.parallel_tool_calls)
        self.assertEqual(request.presence_penalty, 0.3)
        self.assertEqual(request.frequency_penalty, 0.2)
        self.assertEqual(request.seed, 42)
        self.assertEqual(request.service_tier, "default")
        self.assertEqual(request.stop, ["DONE"])
        self.assertEqual(request.user, "tester")
        self.assertEqual(request.logit_bias, {"42": -100})
        self.assertTrue(request.logprobs)
        self.assertEqual(request.top_logprobs, 3)
        self.assertEqual(request.stream_options, {"include_usage": False})

    def test_explicit_reasoning_high_sets_only_canonical_effort(self) -> None:
        payload = ResponsesRequest.model_validate({
            "reasoning": {"effort": "high"},
            "input": "hello",
        })
        request = responses_to_chat_request(payload, "fallback-model")
        self.assertEqual(request.reasoning_effort, "high")
        self.assertIsNone(request.thinking)

    def test_reasoning_low_is_normalized_to_high(self) -> None:
        payload = ResponsesRequest.model_validate({
            "reasoning": {"effort": "low"},
            "input": "hello",
        })
        request = responses_to_chat_request(payload, "fallback-model")
        self.assertEqual(request.reasoning_effort, "high")
        self.assertIsNone(request.thinking)

    def test_reasoning_none_is_preserved_as_none_without_provider_specific_fields(self) -> None:
        payload = ResponsesRequest.model_validate({
            "reasoning": {"effort": "none"},
            "input": "hello",
        })
        request = responses_to_chat_request(payload, "fallback-model")
        self.assertEqual(request.reasoning_effort, "none")
        self.assertIsNone(request.thinking)

    def test_missing_reasoning_keeps_request_reasoning_fields_empty(self) -> None:
        payload = ResponsesRequest.model_validate({
            "input": "hello",
        })
        request = responses_to_chat_request(payload, "fallback-model")
        self.assertIsNone(request.reasoning_effort)
        self.assertIsNone(request.thinking)


class DuplicateCallIdDedupTests(unittest.TestCase):
    """Existing call_ids in messages are skipped on append."""

    def test_function_call_with_existing_call_id_is_skipped(self) -> None:
        payload = ResponsesRequest.model_validate({
            "input": [
                {"type": "function_call", "call_id": "call_1", "name": "get_weather", "arguments": {"city": "Tokyo"}},
                {"type": "function_call_output", "call_id": "call_1", "output": "sunny"},
                {"type": "function_call", "call_id": "call_1", "name": "get_weather", "arguments": {"city": "Tokyo"}},
                {"type": "function_call_output", "call_id": "call_1", "output": "sunny"},
                {"type": "message", "role": "user", "content": "and now?"},
            ]
        })
        # Simulate existing messages that already contain call_1
        existing = [
            ChatMessage(role="user", content="first"),
            ChatMessage(role="assistant", content=None, tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": "{}"}}]),
            ChatMessage(role="tool", tool_call_id="call_1", content="sunny"),
        ]
        request = responses_to_chat_request(payload, "fallback-model", existing_messages=existing)
        messages = [m.model_dump(exclude_none=True) for m in request.messages]
        tool_calls_count = sum(1 for m in messages if m.get("tool_calls"))
        tool_output_count = sum(1 for m in messages if m.get("role") == "tool" and m.get("tool_call_id") == "call_1")
        self.assertLessEqual(tool_calls_count, 1, f"tool_calls should be 0 or 1, got={tool_calls_count}")
        self.assertLessEqual(tool_output_count, 1, f"tool output for call_1 should be 0 or 1, got={tool_output_count}")
        self.assertEqual(messages[-1]["role"], "user")
        self.assertEqual(messages[-1]["content"], "and now?")

    def test_custom_tool_call_with_existing_call_id_is_skipped(self) -> None:
        """custom_tool_call with existing call_id is skipped."""
        payload = ResponsesRequest.model_validate({
            "input": [
                {"type": "custom_tool_call", "id": "ctc_1", "name": "apply_patch", "input": "patch text"},
                {"type": "custom_tool_call_output", "call_id": "ctc_1", "output": "ok"},
                {"type": "custom_tool_call", "id": "ctc_1", "name": "apply_patch", "input": "patch text"},
                {"type": "custom_tool_call_output", "call_id": "ctc_1", "output": "ok"},
                {"type": "message", "role": "user", "content": "next"},
            ]
        })
        existing = [
            ChatMessage(role="user", content="first"),
            ChatMessage(role="assistant", content=None, tool_calls=[
                {"id": "ctc_1", "type": "function", "function": {"name": "apply_patch", "arguments": '{"input":"patch text"}'}},
            ]),
            ChatMessage(role="tool", tool_call_id="ctc_1", content="ok"),
        ]
        request = responses_to_chat_request(payload, "fallback-model", existing_messages=existing)
        messages = [m.model_dump(exclude_none=True) for m in request.messages]
        tool_calls_count = sum(1 for m in messages if m.get("tool_calls"))
        tool_output_count = sum(1 for m in messages if m.get("role") == "tool" and m.get("tool_call_id") == "ctc_1")
        self.assertLessEqual(tool_calls_count, 1, f"custom tool_calls should be 0 or 1, got={tool_calls_count}")
        self.assertLessEqual(tool_output_count, 1, f"custom tool output for ctc_1 should be 0 or 1, got={tool_output_count}")
        self.assertEqual(messages[-1]["role"], "user")
        self.assertEqual(messages[-1]["content"], "next")

    def test_tool_search_call_dedup_also_respects_existing_call_ids(self) -> None:
        """tool_search_call and tool_search_output with existing call_id are skipped."""
        payload = ResponsesRequest.model_validate({
            "input": [
                {"type": "tool_search_call", "call_id": "tsc_1", "query": "find tools"},
                {"type": "tool_search_output", "call_id": "tsc_1", "output": {"results": []}},
                {"type": "tool_search_call", "call_id": "tsc_1", "query": "find tools"},
                {"type": "tool_search_output", "call_id": "tsc_1", "output": {"results": []}},
                {"role": "user", "content": "done"},
            ]
        })
        existing = [
            ChatMessage(role="assistant", content=None, tool_calls=[
                {"id": "tsc_1", "type": "function", "function": {"name": "tool_search_proxy", "arguments": '{"query":"find tools"}'}},
            ]),
            ChatMessage(role="tool", tool_call_id="tsc_1", content='{"call_id":"tsc_1","output":{"results":[]},"type":"tool_search_output"}'),
        ]
        request = responses_to_chat_request(payload, "fallback-model", existing_messages=existing)
        messages = [m.model_dump(exclude_none=True) for m in request.messages]
        # Only check messages ADDED by the payload (after existing messages)
        added_messages = messages[len(existing):]
        tool_calls_count = sum(1 for m in added_messages if m.get("tool_calls"))
        tool_output_count = sum(1 for m in added_messages if m.get("role") == "tool" and m.get("tool_call_id") == "tsc_1")
        self.assertLessEqual(tool_calls_count, 0, f"tool_search calls should be 0, got={tool_calls_count}")
        self.assertLessEqual(tool_output_count, 1, f"tool_search output should be 0 or 1, got={tool_output_count}")
        self.assertEqual(messages[-1]["role"], "user")

    def test_dedup_skips_only_duplicate_and_preserves_new_call_ids(self) -> None:
        """Only duplicate calls skipped; new call_ids are appended normally."""
        payload = ResponsesRequest.model_validate({
            "input": [
                {"type": "function_call", "call_id": "call_old", "name": "get_weather", "arguments": {"city": "Tokyo"}},
                {"type": "function_call_output", "call_id": "call_old", "output": "sunny"},
                {"type": "function_call", "call_id": "call_new", "name": "get_weather", "arguments": {"city": "Osaka"}},
                {"type": "function_call_output", "call_id": "call_new", "output": "rain"},
            ]
        })
        existing = [
            ChatMessage(role="assistant", content=None, tool_calls=[
                {"id": "call_old", "type": "function", "function": {"name": "get_weather", "arguments": "{}"}},
            ]),
            ChatMessage(role="tool", tool_call_id="call_old", content="sunny"),
        ]
        request = responses_to_chat_request(payload, "fallback-model", existing_messages=existing)
        messages = [m.model_dump(exclude_none=True) for m in request.messages]
        # call_old should NOT produce a second tool_call; call_new SHOULD
        # Only check messages added by the payload (after existing)
        added_messages = messages[len(existing):]
        new_tool_calls = [m for m in added_messages if m.get("tool_calls")]
        new_tool_outputs = [m for m in added_messages if m.get("role") == "tool"]
        # The new call_new pair: assistant (with tool_calls) + tool (output)
        self.assertEqual(len(new_tool_calls), 1, f"Expected 1 new tool_call assistant, got {len(new_tool_calls)}")
        self.assertEqual(len(new_tool_outputs), 1, f"Expected 1 new tool output, got {len(new_tool_outputs)}")
        call_ids = set()
        for tc_list in [m.get("tool_calls", []) for m in new_tool_calls]:
            for tc in tc_list:
                cid = tc.get("id") or tc.get("call_id")
                if cid:
                    call_ids.add(cid)
        self.assertIn("call_new", call_ids)
        self.assertNotIn("call_old", call_ids)


if __name__ == "__main__":
    unittest.main()
