from __future__ import annotations

from collections.abc import AsyncIterator
import json
from typing import Any

from .bridge_context import BridgeToolContext
from .protocol.sse import extract_block, parse_sse_block
from .stream_responses_state import ResponsesStreamState


def _extract_reasoning_delta(delta: dict) -> str:
    for key in ("reasoning_content", "reasoning"):
        value = delta.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


async def create_responses_sse_stream_from_chat_stream(
    upstream_stream: AsyncIterator[bytes],
    tool_context: BridgeToolContext | None = None,
    response_id: str | None = None,
    original_request: dict | None = None,
    _captured_state: list | None = None,
) -> AsyncIterator[bytes]:
    """Wrap a Chat Completions SSE stream into Responses SSE events.

    If _captured_state is provided (e.g. []), the state is appended
    after iteration so caller can extract assistant_message for session.
    """
    buffer = ""
    state = ResponsesStreamState(tool_context, response_id=response_id)
    state.envelope.set_request_echo(original_request)
    try:
        async for chunk in upstream_stream:
            buffer += chunk.decode("utf-8", errors="ignore")
            while True:
                extracted = extract_block(buffer)
                if extracted is None:
                    break
                block, buffer = extracted
                if not block.strip():
                    continue
                event_name, data = parse_sse_block(block)
                if not data:
                    continue
                if data.strip() == "[DONE]":
                    for event in state.finalize():
                        yield event
                    continue
                payload = json.loads(data)
                for event in _process_chat_chunk(payload, event_name, state):
                    yield event

        for event in state.finalize():
            yield event
    except Exception as exc:
        yield state.fail(f"Stream error: {exc}")
    finally:
        if _captured_state is not None:
            _captured_state.append(state)


def _process_chat_chunk(
    payload: dict, event_name: str | None, state: ResponsesStreamState
) -> list[bytes]:
    """Process a single Chat Completions chunk through the state machine.
    Returns list of SSE event bytes. Used by both streaming and non-streaming paths."""
    events: list[bytes] = []

    if event_name == "error" or payload.get("error"):
        err = payload.get("error") or payload
        message = err.get("message") if isinstance(err, dict) else str(err)
        error_type = err.get("type", "stream_error") if isinstance(err, dict) else "stream_error"
        return [state.fail(message or "upstream stream error", error_type)]

    state.apply_chunk_metadata(payload)
    events.extend(state.ensure_started())

    choice = (payload.get("choices") or [{}])[0]
    delta = choice.get("delta") or {}

    reasoning_delta = _extract_reasoning_delta(delta)
    if reasoning_delta:
        events.extend(state.push_reasoning_delta(reasoning_delta))

    tool_calls = delta.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        reasoning_text = state.active_reasoning_text_for_tools()
        if reasoning_text:
            events.extend(state.finalize_reasoning_if_open())
        # If inline think is still in detecting/reasoning phase, flush it
        events.extend(state.inline_think.force_to_text(state))
        for tool_call in tool_calls:
            if isinstance(tool_call, dict):
                events.extend(state.push_tool_call_delta(tool_call, reasoning_text or None))

    content = delta.get("content")
    if isinstance(content, str) and content:
        # If explicit reasoning field was already used, finalize it and
        # skip inline think detection — content is ordinary text
        if reasoning_delta:
            events.extend(state.finalize_reasoning_if_open())
            events.extend(state.push_text_delta(content))
        else:
            events.extend(state.push_content_delta(content))
    elif isinstance(content, list):
        events.extend(state.finalize_reasoning_if_open())
        # Flush any pending inline think buffer as text
        events.extend(state.inline_think.force_to_text(state))
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type in {"text", "output_text"} and isinstance(part.get("text"), str) and part.get("text"):
                # Forward annotations from structured content parts
                state.message.add_annotations(part.get("annotations"))
                events.extend(state.push_text_delta(part["text"]))
            elif part_type == "refusal" and isinstance(part.get("refusal"), str) and part.get("refusal"):
                events.extend(state.push_refusal_part(part["refusal"]))

    refusal = delta.get("refusal")
    if isinstance(refusal, str) and refusal:
        events.extend(state.finalize_reasoning_if_open())
        events.extend(state.push_refusal_part(refusal))

    finish_reason = choice.get("finish_reason")
    if isinstance(finish_reason, str) and finish_reason:
        state.set_finish_reason(finish_reason)

    return events


def _chat_message_to_fake_delta(chat_choice: dict) -> dict:
    """Convert a non-streaming Chat Completions choice into a fake streaming delta
    so _process_chat_chunk can produce the same SSE events.

    Critical: injects 'index' into each tool_call because the streaming delta
    protocol uses 'index' to distinguish parallel tool calls, but the non-streaming
    message format omits it. Without this, all parallel tool_calls collapse to index 0.
    """
    message = chat_choice.get("message") or {}
    delta: dict = {
        "content": message.get("content") or "",
        "role": "assistant",
    }
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        delta["tool_calls"] = [
            {**tc, "index": i} for i, tc in enumerate(tool_calls)
        ]
    reasoning = message.get("reasoning_content") or message.get("reasoning")
    if reasoning:
        delta["reasoning_content"] = reasoning
    return delta


async def create_responses_sse_from_chat_response(
    chat_body: dict,
    tool_context: BridgeToolContext | None = None,
    response_id: str | None = None,
    original_request: dict | None = None,
) -> AsyncIterator[bytes]:
    """Wrap a non-streaming Chat Completions response into Responses SSE events."""
    state = ResponsesStreamState(tool_context, response_id=response_id)
    state.envelope.set_request_echo(original_request)
    state.apply_chunk_metadata(chat_body)

    choices = chat_body.get("choices") or []
    for choice in choices:
        delta = _chat_message_to_fake_delta(choice)
        chunk = {"choices": [{"delta": delta, "finish_reason": choice.get("finish_reason")}]}
        for event in _process_chat_chunk(chunk, None, state):
            yield event

    for event in state.finalize():
        yield event
