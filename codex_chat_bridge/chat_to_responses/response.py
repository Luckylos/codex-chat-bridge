from __future__ import annotations

from typing import Any

from ..bridge_context import BridgeToolContext
from ..models import ResponsesResponse
from ..response_semantics import map_chat_usage, response_status_from_finish_reason
from .common import extract_reasoning_text, message_content_parts, output_text_from_parts
from .tools import chat_tool_calls_to_response_items


def chat_text_to_responses(
    chat_body: dict[str, Any],
    fallback_model: str,
    tool_context: BridgeToolContext | None = None,
) -> ResponsesResponse:
    choice = (chat_body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    reasoning_text = extract_reasoning_text(message)
    response_id = chat_body.get("id") or "resp_bridge"
    model = chat_body.get("model") or fallback_model
    finish_reason = choice.get("finish_reason") if isinstance(choice.get("finish_reason"), str) else None
    created_at = chat_body.get("created") if isinstance(chat_body.get("created"), int) else None

    output: list[dict[str, Any]] = []
    if reasoning_text:
        output.append(
            {
                "id": f"rs_{response_id}",
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": reasoning_text}],
            }
        )

    parts = message_content_parts(message)
    output_text = output_text_from_parts(parts)
    if parts:
        output.append(
            {
                "id": f"msg_{response_id}",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": parts,
            }
        )

    output.extend(chat_tool_calls_to_response_items(message, reasoning_text, tool_context or BridgeToolContext()))

    response = ResponsesResponse(
        id=f"resp_{response_id}",
        status=response_status_from_finish_reason(finish_reason),
        model=model,
        output=output,
        output_text=output_text,
        created_at=created_at,
        usage=map_chat_usage(chat_body.get("usage") if isinstance(chat_body.get("usage"), dict) else None),
    )
    if finish_reason == "length":
        response.incomplete_details = {"reason": "max_output_tokens"}
    return response
