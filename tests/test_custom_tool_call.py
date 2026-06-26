from __future__ import annotations

import asyncio
import json
import unittest

from codex_chat_bridge.models import ResponsesRequest
from codex_chat_bridge.stream_chat_to_responses import create_responses_sse_stream_from_chat_stream
from codex_chat_bridge.tool_context import build_tool_context_from_request
from codex_chat_bridge.transform_chat_to_responses import chat_text_to_responses
from codex_chat_bridge.transform_responses_to_chat import responses_to_chat_request


class CustomToolCallTests(unittest.TestCase):
    def test_responses_request_to_chat_maps_custom_tool_and_choice(self) -> None:
        payload = ResponsesRequest.model_validate(
            {
                "model": "demo-model",
                "tools": [
                    {
                        "type": "custom",
                        "name": "apply_patch",
                        "description": "Apply a patch to files.",
                    }
                ],
                "tool_choice": {"type": "custom", "name": "apply_patch"},
                "input": [
                    {
                        "type": "custom_tool_call",
                        "id": "ctc_1",
                        "call_id": "call_patch",
                        "name": "apply_patch",
                        "input": "*** Begin Patch\n*** End Patch",
                    }
                ],
            }
        )

        request = responses_to_chat_request(payload, "fallback-model")
        tool = request.tools[0]
        tool_call = request.messages[0].tool_calls[0]

        self.assertEqual(tool["function"]["name"], "apply_patch")
        self.assertEqual(tool["function"]["parameters"]["required"], ["input"])
        self.assertEqual(request.tool_choice, {"type": "function", "function": {"name": "apply_patch"}})
        self.assertEqual(tool_call["function"]["arguments"], '{"input":"*** Begin Patch\\n*** End Patch"}')

    def test_custom_tool_call_output_maps_to_tool_message_with_full_item_json(self) -> None:
        payload = ResponsesRequest.model_validate(
            {
                "input": [
                    {
                        "type": "custom_tool_call_output",
                        "call_id": "call_patch",
                        "output": "ok",
                        "metadata": {"exit_code": 0},
                    }
                ]
            }
        )

        request = responses_to_chat_request(payload, "fallback-model")
        message = request.messages[0].model_dump(exclude_none=True)

        self.assertEqual(message["role"], "tool")
        self.assertEqual(message["tool_call_id"], "call_patch")
        self.assertEqual(
            message["content"],
            '{"call_id":"call_patch","metadata":{"exit_code":0},"output":"ok","type":"custom_tool_call_output"}',
        )

    def test_chat_response_to_responses_restores_custom_tool_call(self) -> None:
        payload = ResponsesRequest.model_validate(
            {
                "model": "demo-model",
                "tools": [{"type": "custom", "name": "apply_patch"}],
                "input": "Patch it.",
            }
        )
        tool_context = build_tool_context_from_request(payload)
        chat_body = {
            "id": "chatcmpl_custom",
            "object": "chat.completion",
            "created": 123,
            "model": "demo-model",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_patch",
                                "type": "function",
                                "function": {
                                    "name": "apply_patch",
                                    "arguments": '{"input":"*** Begin Patch\\n*** End Patch"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }

        response = chat_text_to_responses(chat_body, "fallback-model", tool_context)
        item = response.output[0]

        self.assertEqual(item["type"], "custom_tool_call")
        self.assertEqual(item["id"], "ctc_call_patch")
        self.assertEqual(item["call_id"], "call_patch")
        self.assertEqual(item["name"], "apply_patch")
        self.assertEqual(item["input"], "*** Begin Patch\n*** End Patch")

    def test_stream_restores_custom_tool_input_events(self) -> None:
        payload = ResponsesRequest.model_validate(
            {
                "model": "demo-model",
                "tools": [{"type": "custom", "name": "exec"}],
            }
        )
        tool_context = build_tool_context_from_request(payload)

        async def upstream_stream():
            payloads = [
                {
                    "id": "chatcmpl_custom",
                    "model": "demo-model",
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call_custom",
                                "type": "function",
                                "function": {"name": "exec"},
                            }]
                        }
                    }],
                },
                {
                    "id": "chatcmpl_custom",
                    "model": "demo-model",
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "function": {"arguments": '{\"input\":\"'}
                            }]
                        }
                    }],
                },
                {
                    "id": "chatcmpl_custom",
                    "model": "demo-model",
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "function": {"arguments": 'ls -la\"}'}
                            }]
                        },
                        "finish_reason": "tool_calls",
                    }],
                },
            ]
            for payload in payloads:
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
            yield b'data: [DONE]\n\n'

        async def collect() -> str:
            parts: list[str] = []
            async for chunk in create_responses_sse_stream_from_chat_stream(upstream_stream(), tool_context):
                parts.append(chunk.decode())
            return "".join(parts)

        output = asyncio.run(collect())
        self.assertIn("event: response.custom_tool_call_input.delta", output)
        self.assertIn("event: response.custom_tool_call_input.done", output)
        self.assertNotIn("event: response.function_call_arguments.delta", output)
        self.assertNotIn("event: response.function_call_arguments.done", output)
        compact_output = output.replace(' ', '')
        self.assertIn('"id":"ctc_call_custom"', compact_output)
        self.assertIn('"type":"custom_tool_call"', compact_output)
        self.assertIn('"name":"exec"', compact_output)
        self.assertIn('"input":"ls-la"', compact_output)


if __name__ == "__main__":
    unittest.main()
