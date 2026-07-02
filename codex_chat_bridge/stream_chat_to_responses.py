from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from .bridge_context import BridgeToolContext
from .protocol.sse import extract_block, parse_sse_block
from .stream_responses_state import ResponsesStreamState


_LOGGER = logging.getLogger("codex-chat-bridge")


def _new_stream_state(
    tool_context: BridgeToolContext | None,
    response_id: str | None,
    original_request: dict | None,
) -> ResponsesStreamState:
    state = ResponsesStreamState(tool_context, response_id=response_id)
    state.envelope.set_request_echo(original_request)
    return state


async def _iter_sse_messages(upstream_stream: AsyncIterator[bytes]) -> AsyncIterator[tuple[str | None, str]]:
    buffer = ""
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
            yield event_name, data


def _flush_reasoning_and_inline_think(state: ResponsesStreamState) -> list[bytes]:
    events: list[bytes] = []
    events.extend(state.finalize_reasoning_if_open())
    events.extend(state.inline_think.force_to_text(state))
    return events


def _error_events(
    payload: dict,
    event_name: str | None,
    state: ResponsesStreamState,
) -> list[bytes] | None:
    if event_name != "error" and not payload.get("error"):
        return None
    err = payload.get("error") or payload
    message = err.get("message") if isinstance(err, dict) else str(err)
    error_type = err.get("type", "stream_error") if isinstance(err, dict) else "stream_error"
    return state.fail(message or "upstream stream error", error_type)


def _tool_call_events(
    state: ResponsesStreamState,
    tool_calls: list,
) -> list[bytes]:
    events: list[bytes] = []
    reasoning_text = state.active_reasoning_text_for_tools()
    if reasoning_text:
        events.extend(state.finalize_reasoning_if_open())
    events.extend(state.inline_think.force_to_text(state))
    for tool_call in tool_calls:
        if isinstance(tool_call, dict):
            events.extend(state.push_tool_call_delta(tool_call, reasoning_text or None))
    return events


def _string_content_events(
    state: ResponsesStreamState,
    content: str,
    *,
    reasoning_delta: str,
) -> list[bytes]:
    if not content:
        return []
    if reasoning_delta:
        return [*state.finalize_reasoning_if_open(), *state.push_text_delta(content)]
    return state.push_content_delta(content)


def _structured_content_events(
    state: ResponsesStreamState,
    content: list,
) -> list[bytes]:
    events = _flush_reasoning_and_inline_think(state)
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type in {"text", "output_text"} and isinstance(part.get("text"), str) and part.get("text"):
            state.message.add_annotations(part.get("annotations"))
            events.extend(state.push_text_delta(part["text"]))
        elif part_type == "refusal" and isinstance(part.get("refusal"), str) and part.get("refusal"):
            events.extend(state.push_refusal_part(part["refusal"]))
        else:
            _LOGGER.debug("Skipping unhandled structured content part type: %s", part_type)
    return events


def _process_sse_message(
    event_name: str | None,
    data: str,
    state: ResponsesStreamState,
) -> list[bytes]:
    if data.strip() == "[DONE]":
        return state.finalize()
    payload = json.loads(data)
    return _process_chat_chunk(payload, event_name, state)


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
    state = _new_stream_state(tool_context, response_id, original_request)
    try:
        async for event_name, data in _iter_sse_messages(upstream_stream):
            for event in _process_sse_message(event_name, data, state):
                yield event

        for event in state.finalize():
            yield event
    except Exception as exc:
        for event in state.fail(f"Stream error: {exc}"):
            yield event
    finally:
        if _captured_state is not None:
            _captured_state.append(state)


def _process_chat_chunk(
    payload: dict,
    event_name: str | None,
    state: ResponsesStreamState,
) -> list[bytes]:
    """Process a single Chat Completions chunk through the state machine.
    Returns list of SSE event bytes. Used by both streaming and non-streaming paths."""
    error_events = _error_events(payload, event_name, state)
    if error_events is not None:
        return error_events

    state.apply_chunk_metadata(payload)
    events = state.ensure_started()
    choice = (payload.get("choices") or [{}])[0]
    delta = choice.get("delta") or {}

    reasoning_delta = ""
    for key in ("reasoning_content", "reasoning"):
        value = delta.get(key)
        if isinstance(value, str) and value:
            reasoning_delta = value
            break
    if reasoning_delta:
        events.extend(state.push_reasoning_delta(reasoning_delta))

    tool_calls = delta.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        events.extend(_tool_call_events(state, tool_calls))

    state.message.add_annotations(delta.get("annotations"))

    content = delta.get("content")
    if isinstance(content, str):
        events.extend(_string_content_events(state, content, reasoning_delta=reasoning_delta))
    elif isinstance(content, list):
        events.extend(_structured_content_events(state, content))

    refusal = delta.get("refusal")
    if isinstance(refusal, str) and refusal:
        events.extend(state.finalize_reasoning_if_open())
        events.extend(state.push_refusal_part(refusal))

    finish_reason = choice.get("finish_reason")
    if isinstance(finish_reason, str) and finish_reason:
        state.set_finish_reason(finish_reason)
    return events


def _indexed_tool_calls(tool_calls: list) -> list[dict]:
    return [{**tc, "index": i} for i, tc in enumerate(tool_calls) if isinstance(tc, dict)]


def _chat_message_to_fake_delta(chat_choice: dict) -> dict:
    """Convert a non-streaming Chat Completions choice into a fake streaming delta
    so _process_chat_chunk can produce the same SSE events.

    Critical: injects 'index' into each tool_call because the streaming delta
    protocol uses 'index' to distinguish parallel tool calls, but the non-streaming
    message format omits it. Without this, all parallel tool_calls collapse to index 0.
    """
    message = chat_choice.get("message") or {}
    delta: dict = {
        "content": message.get("content"),
        "role": "assistant",
        "refusal": message.get("refusal"),
        "annotations": message.get("annotations"),
        "tool_calls": message.get("tool_calls"),
        "reasoning_content": message.get("reasoning_content") or message.get("reasoning"),
    }
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        delta["tool_calls"] = _indexed_tool_calls(tool_calls)
    return delta


async def create_responses_sse_from_chat_response(
    chat_body: dict,
    tool_context: BridgeToolContext | None = None,
    response_id: str | None = None,
    original_request: dict | None = None,
) -> AsyncIterator[bytes]:
    """Wrap a non-streaming Chat Completions response into Responses SSE events."""
    state = _new_stream_state(tool_context, response_id, original_request)
    state.apply_chunk_metadata(chat_body)

    choices = chat_body.get("choices") or []
    for choice in choices:
        delta = _chat_message_to_fake_delta(choice)
        chunk = {"choices": [{"delta": delta, "finish_reason": choice.get("finish_reason")}]}
        for event in _process_chat_chunk(chunk, None, state):
            yield event

    for event in state.finalize():
        yield event
