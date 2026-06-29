from __future__ import annotations

from typing import Any

from ..bridge_context import BridgeToolContext
from ..models import ResponsesResponse
from ..response_semantics import map_chat_usage, response_status_from_finish_reason, incomplete_reason_from_finish_reason
from .common import extract_reasoning_text, message_content_parts, output_text_from_parts
from .tools import chat_tool_calls_to_response_items

# Fields echoed from the original Responses request into the response object
# per the OpenAI Responses API specification.
_REQUEST_ECHO_FIELDS = (
    "instructions",
    "max_output_tokens",
    "parallel_tool_calls",
    "previous_response_id",
    "reasoning",
    "temperature",
    "tool_choice",
    "tools",
    "top_p",
    "metadata",
)


def _echo_request_fields(response: ResponsesResponse, original_request: dict | None) -> None:
    """Copy request-echo fields from the original request into the response."""
    if not original_request:
        return
    for key in _REQUEST_ECHO_FIELDS:
        value = original_request.get(key)
        if value is not None:
            setattr(response, key, value)


def chat_text_to_responses(
    chat_body: dict[str, Any],
    fallback_model: str,
    tool_context: BridgeToolContext | None = None,
    original_request: dict[str, Any] | None = None,
) -> ResponsesResponse:
    choice = (chat_body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    # When n>1, Chat returns multiple choices — the Responses API only supports
    # a single response object, so we take the first choice and warn if others exist.
    all_choices = chat_body.get("choices") or []
    if len(all_choices) > 1:
        import logging
        logging.getLogger("codex-chat-bridge").warning(
            "Chat Completions returned %d choices (n>1); Responses API only supports one response — using first choice",
            len(all_choices),
        )
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
    response.incomplete_details = incomplete_reason_from_finish_reason(finish_reason)
    _echo_request_fields(response, original_request)
    return response
