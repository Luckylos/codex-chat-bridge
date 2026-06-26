from __future__ import annotations

import asyncio
import json
import unittest

from codex_chat_bridge.stream_chat_to_responses import create_responses_sse_stream_from_chat_stream


class StreamingContentRefusalSemanticsTests(unittest.TestCase):
    def test_stream_restores_text_parts_from_content_array(self) -> None:
        async def upstream_stream():
            payloads = [
                {
                    "id": "chatcmpl_content_array",
                    "model": "demo-model",
                    "choices": [{"delta": {"content": [{"type": "output_text", "text": "Hel"}]}, "finish_reason": None}],
                },
                {
                    "id": "chatcmpl_content_array",
                    "model": "demo-model",
                    "choices": [{"delta": {"content": [{"type": "output_text", "text": "lo"}]}, "finish_reason": "stop"}],
                },
            ]
            for payload in payloads:
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
            yield b"data: [DONE]\n\n"

        async def collect() -> str:
            parts: list[str] = []
            async for chunk in create_responses_sse_stream_from_chat_stream(upstream_stream()):
                parts.append(chunk.decode())
            return "".join(parts)

        output = asyncio.run(collect())
        compact = output.replace(" ", "")
        self.assertIn("event:response.output_text.delta", compact)
        self.assertIn('"text":"Hello"', compact)
        self.assertIn("event:response.completed", compact)

    def test_stream_restores_refusal_parts_from_content_array(self) -> None:
        async def upstream_stream():
            payloads = [
                {
                    "id": "chatcmpl_refusal_array",
                    "model": "demo-model",
                    "choices": [{"delta": {"content": [{"type": "refusal", "refusal": "No."}]}, "finish_reason": "stop"}],
                }
            ]
            for payload in payloads:
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
            yield b"data: [DONE]\n\n"

        async def collect() -> str:
            parts: list[str] = []
            async for chunk in create_responses_sse_stream_from_chat_stream(upstream_stream()):
                parts.append(chunk.decode())
            return "".join(parts)

        output = asyncio.run(collect())
        compact = output.replace(" ", "")
        self.assertIn("event:response.content_part.added", compact)
        self.assertIn("event:response.content_part.done", compact)
        self.assertNotIn("event:response.output_text.delta", compact)
        self.assertIn('"type":"refusal"', compact)
        self.assertIn('"refusal":"No."', compact)
        self.assertIn("event:response.completed", compact)

    def test_stream_keeps_refusal_before_later_text(self) -> None:
        async def upstream_stream():
            payloads = [
                {
                    "id": "chatcmpl_refusal_then_text",
                    "model": "demo-model",
                    "choices": [{"delta": {"content": [{"type": "refusal", "refusal": "No."}]}, "finish_reason": None}],
                },
                {
                    "id": "chatcmpl_refusal_then_text",
                    "model": "demo-model",
                    "choices": [{"delta": {"content": "But safe alternative."}, "finish_reason": "stop"}],
                },
            ]
            for payload in payloads:
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
            yield b"data: [DONE]\n\n"

        async def collect() -> str:
            parts: list[str] = []
            async for chunk in create_responses_sse_stream_from_chat_stream(upstream_stream()):
                parts.append(chunk.decode())
            return "".join(parts)

        output = asyncio.run(collect())
        compact = output.replace(" ", "")
        self.assertIn('"content_index":1', compact)
        self.assertIn('"refusal":"No."', compact)
        self.assertIn('"text":"Butsafealternative."', compact)
        self.assertIn('"type":"message"', compact)


if __name__ == "__main__":
    unittest.main()
