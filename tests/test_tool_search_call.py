from __future__ import annotations

import asyncio
import json
import unittest

from codex_chat_bridge.models import ResponsesRequest
from codex_chat_bridge.stream_chat_to_responses import create_responses_sse_stream_from_chat_stream
from codex_chat_bridge.tool_context import TOOL_SEARCH_PROXY_NAME, build_tool_context_from_request, flatten_namespace_tool_name
from codex_chat_bridge.transform_chat_to_responses import chat_text_to_responses
from codex_chat_bridge.transform_responses_to_chat import responses_to_chat_request


class ToolSearchCallTests(unittest.TestCase):
    def test_responses_request_exposes_tool_search_and_loaded_namespace_tools(self) -> None:
        payload = ResponsesRequest.model_validate(
            {
                "model": "demo-model",
                "tools": [{"type": "tool_search"}],
                "input": [
                    {
                        "type": "tool_search_call",
                        "call_id": "call_tool_search_1",
                        "status": "completed",
                        "execution": "client",
                        "arguments": {"query": "Gmail search emails", "limit": 5},
                    },
                    {
                        "type": "tool_search_output",
                        "call_id": "call_tool_search_1",
                        "status": "completed",
                        "execution": "client",
                        "tools": [
                            {
                                "type": "namespace",
                                "name": "mcp__codex_apps__gmail",
                                "description": "Find and reference emails from your inbox.",
                                "tools": [
                                    {
                                        "type": "function",
                                        "name": "_search_emails",
                                        "description": "Search Gmail for emails matching a query.",
                                        "parameters": {
                                            "type": "object",
                                            "properties": {
                                                "query": {"type": "string"},
                                                "max_results": {"type": "integer"},
                                            },
                                            "required": ["query"],
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {"type": "message", "role": "user", "content": "Search unread inbox mail."},
                ],
            }
        )

        request = responses_to_chat_request(payload, "fallback-model")
        tool_names = [tool["function"]["name"] for tool in request.tools]

        self.assertIn(TOOL_SEARCH_PROXY_NAME, tool_names)
        self.assertIn("mcp__codex_apps__gmail___search_emails", tool_names)
        self.assertEqual(request.messages[0].tool_calls[0]["function"]["name"], TOOL_SEARCH_PROXY_NAME)
        self.assertEqual(request.messages[1].role, "tool")
        self.assertEqual(request.messages[1].tool_call_id, "call_tool_search_1")
        self.assertIn("mcp__codex_apps__gmail", request.messages[1].content)

    def test_chat_response_restores_tool_search_call(self) -> None:
        payload = ResponsesRequest.model_validate(
            {
                "model": "demo-model",
                "tools": [{"type": "tool_search"}],
                "input": "Find tools.",
            }
        )
        tool_context = build_tool_context_from_request(payload)
        chat_body = {
            "id": "chatcmpl_tool_search",
            "object": "chat.completion",
            "created": 123,
            "model": "demo-model",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_tool_search_1",
                                "type": "function",
                                "function": {
                                    "name": TOOL_SEARCH_PROXY_NAME,
                                    "arguments": '{"query":"Gmail search emails","limit":10}',
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
        self.assertEqual(item["type"], "tool_search_call")
        self.assertEqual(item["call_id"], "call_tool_search_1")
        self.assertEqual(item["execution"], "client")
        self.assertEqual(item["arguments"]["query"], "Gmail search emails")
        self.assertEqual(item["arguments"]["limit"], 10)

    def test_chat_response_restores_namespace_function_after_tool_search_output(self) -> None:
        payload = ResponsesRequest.model_validate(
            {
                "model": "demo-model",
                "tools": [{"type": "tool_search"}],
                "input": [
                    {
                        "type": "tool_search_output",
                        "call_id": "call_tool_search_1",
                        "status": "completed",
                        "execution": "client",
                        "tools": [
                            {
                                "type": "namespace",
                                "name": "mcp__codex_apps__gmail",
                                "tools": [
                                    {
                                        "type": "function",
                                        "name": "_search_emails",
                                        "parameters": {
                                            "type": "object",
                                            "properties": {"query": {"type": "string"}},
                                            "required": ["query"],
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        )
        tool_context = build_tool_context_from_request(payload)
        chat_body = {
            "id": "chatcmpl_namespace",
            "object": "chat.completion",
            "created": 123,
            "model": "demo-model",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_ns_1",
                                "type": "function",
                                "function": {
                                    "name": "mcp__codex_apps__gmail___search_emails",
                                    "arguments": '{"query":"-in:spam -in:trash","max_results":5}',
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
        self.assertEqual(item["type"], "function_call")
        self.assertEqual(item["name"], "_search_emails")
        self.assertEqual(item["namespace"], "mcp__codex_apps__gmail")
        self.assertEqual(item["arguments"], '{"max_results":5,"query":"-in:spam -in:trash"}')

    def test_long_namespace_tool_names_use_hashed_suffix_mapping(self) -> None:
        namespace = "mcp__codex_apps__" + ("verylongnamespace_" * 3)
        tool_name = "_" + ("very_long_tool_name_" * 3)
        expected_chat_name = flatten_namespace_tool_name(namespace, tool_name)
        payload = ResponsesRequest.model_validate(
            {
                "model": "demo-model",
                "tools": [{"type": "tool_search"}],
                "input": [
                    {
                        "type": "tool_search_output",
                        "call_id": "call_tool_search_long",
                        "status": "completed",
                        "execution": "client",
                        "tools": [
                            {
                                "type": "namespace",
                                "name": namespace,
                                "tools": [
                                    {
                                        "type": "function",
                                        "name": tool_name,
                                        "parameters": {
                                            "type": "object",
                                            "properties": {"query": {"type": "string"}},
                                            "required": ["query"],
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        )
        self.assertLessEqual(len(expected_chat_name), 64)
        self.assertRegex(expected_chat_name, r"__([0-9a-f]{16})$")

        request = responses_to_chat_request(payload, "fallback-model")
        tool_names = [tool["function"]["name"] for tool in request.tools]
        self.assertIn(expected_chat_name, tool_names)

        tool_context = build_tool_context_from_request(payload)
        chat_body = {
            "id": "chatcmpl_namespace_long",
            "object": "chat.completion",
            "created": 123,
            "model": "demo-model",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_ns_long_1",
                                "type": "function",
                                "function": {
                                    "name": expected_chat_name,
                                    "arguments": '{"query":"hello"}',
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
        self.assertEqual(item["name"], tool_name)
        self.assertEqual(item["namespace"], namespace)

    def test_stream_restores_tool_search_call(self) -> None:
        payload = ResponsesRequest.model_validate(
            {
                "model": "demo-model",
                "tools": [{"type": "tool_search"}],
                "input": "Search for Gmail tools.",
            }
        )
        tool_context = build_tool_context_from_request(payload)

        async def upstream_stream():
            payloads = [
                {
                    "id": "chatcmpl_tool_search",
                    "model": "demo-model",
                    "choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_tool_search_1", "type": "function", "function": {"name": TOOL_SEARCH_PROXY_NAME}}]}}],
                },
                {
                    "id": "chatcmpl_tool_search",
                    "model": "demo-model",
                    "choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"query":"Gmail search emails","limit":10}'}}]}, "finish_reason": "tool_calls"}],
                },
            ]
            for payload in payloads:
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
            yield b"data: [DONE]\n\n"

        async def collect() -> str:
            parts: list[str] = []
            async for chunk in create_responses_sse_stream_from_chat_stream(upstream_stream(), tool_context):
                parts.append(chunk.decode())
            return "".join(parts)

        output = asyncio.run(collect())
        compact = output.replace(" ", "")
        self.assertIn('"type":"tool_search_call"', compact)
        self.assertIn('"execution":"client"', compact)
        self.assertIn('"call_id":"call_tool_search_1"', compact)
        self.assertIn('"query":"Gmailsearchemails"', compact.replace(" ", ""))
        self.assertIn('event:response.function_call_arguments.done', compact)


if __name__ == "__main__":
    unittest.main()
