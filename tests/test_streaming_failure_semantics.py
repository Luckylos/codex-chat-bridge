from __future__ import annotations

import asyncio
import json
import unittest

from codex_chat_bridge.stream_chat_to_responses import create_responses_sse_stream_from_chat_stream


class StreamingFailureSemanticsTests(unittest.TestCase):
    def test_failed_event_keeps_completed_output_items(self) -> None:
        async def upstream_stream():
            chunks = [
                {
                    "id": "chatcmpl_partial_fail",
                    "model": "demo-model",
                    "choices": [{"delta": {"reasoning_content": "Need context."}, "finish_reason": None}],
                },
                {
                    "id": "chatcmpl_partial_fail",
                    "model": "demo-model",
                    "choices": [{"delta": {"content": "hello"}, "finish_reason": None}],
                },
            ]
            for payload in chunks:
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
            yield b'event: error\ndata: {"error":{"message":"bad request","type":"invalid_request_error"}}\n\n'

        async def collect() -> str:
            parts: list[str] = []
            async for chunk in create_responses_sse_stream_from_chat_stream(upstream_stream()):
                parts.append(chunk.decode())
            return "".join(parts)

        output = asyncio.run(collect())
        compact = output.replace(" ", "")
        self.assertIn("event:response.failed", compact)
        self.assertNotIn("event:response.completed", compact)
        self.assertIn("event:response.output_item.done", compact)
        self.assertIn('"type":"reasoning"', compact)
        self.assertIn('"text":"Needcontext."', compact)
        self.assertIn('"message":"badrequest"', compact)
        self.assertIn('"type":"invalid_request_error"', compact)
        self.assertLess(compact.index("event:response.output_item.done"), compact.index("event:response.failed"))

    def test_stream_parser_handles_sse_frame_split_across_chunks(self) -> None:
        payload = json.dumps(
            {
                "id": "chatcmpl_split",
                "model": "demo-model",
                "choices": [{"delta": {"content": "Hello"}, "finish_reason": "stop"}],
            },
            ensure_ascii=False,
        )

        async def upstream_stream():
            yield f"data: {payload[:40]}".encode()
            yield f"{payload[40:]}\n\n".encode()
            yield b"data: [DONE]\n\n"

        async def collect() -> str:
            parts: list[str] = []
            async for chunk in create_responses_sse_stream_from_chat_stream(upstream_stream()):
                parts.append(chunk.decode())
            return "".join(parts)

        output = asyncio.run(collect())
        compact = output.replace(" ", "")
        self.assertIn('"text":"Hello"', compact)
        self.assertIn("event:response.completed", compact)


if __name__ == "__main__":
    unittest.main()
