from __future__ import annotations

from typing import Any

from ..protocol.sse import sse_event


def response_event(event_name: str, response: dict) -> bytes:
    return sse_event(event_name, {"type": event_name, "response": response})


def output_item_added(output_index: int | None, item: dict) -> bytes:
    return sse_event(
        "response.output_item.added",
        {"type": "response.output_item.added", "output_index": output_index, "item": item},
    )


def output_item_done(output_index: int | None, item: dict) -> bytes:
    return sse_event(
        "response.output_item.done",
        {"type": "response.output_item.done", "output_index": output_index, "item": item},
    )


def content_part_added(item_id: str, output_index: int | None, content_index: int, part: dict) -> bytes:
    return sse_event(
        "response.content_part.added",
        {
            "type": "response.content_part.added",
            "item_id": item_id,
            "output_index": output_index,
            "content_index": content_index,
            "part": part,
        },
    )


def content_part_done(item_id: str, output_index: int | None, content_index: int, part: dict) -> bytes:
    return sse_event(
        "response.content_part.done",
        {
            "type": "response.content_part.done",
            "item_id": item_id,
            "output_index": output_index,
            "content_index": content_index,
            "part": part,
        },
    )


def output_text_delta(item_id: str, output_index: int | None, content_index: int, delta: str) -> bytes:
    return sse_event(
        "response.output_text.delta",
        {
            "type": "response.output_text.delta",
            "item_id": item_id,
            "output_index": output_index,
            "content_index": content_index,
            "delta": delta,
        },
    )


def output_text_done(item_id: str, output_index: int | None, content_index: int, text: str) -> bytes:
    return sse_event(
        "response.output_text.done",
        {
            "type": "response.output_text.done",
            "item_id": item_id,
            "output_index": output_index,
            "content_index": content_index,
            "text": text,
        },
    )


def reasoning_summary_part_added(item_id: str, output_index: int | None, summary_index: int, part: dict) -> bytes:
    return sse_event(
        "response.reasoning_summary_part.added",
        {
            "type": "response.reasoning_summary_part.added",
            "item_id": item_id,
            "output_index": output_index,
            "summary_index": summary_index,
            "part": part,
        },
    )


def reasoning_summary_text_delta(item_id: str, output_index: int | None, summary_index: int, delta: str) -> bytes:
    return sse_event(
        "response.reasoning_summary_text.delta",
        {
            "type": "response.reasoning_summary_text.delta",
            "item_id": item_id,
            "output_index": output_index,
            "summary_index": summary_index,
            "delta": delta,
        },
    )


def reasoning_summary_text_done(item_id: str, output_index: int | None, summary_index: int, text: str) -> bytes:
    return sse_event(
        "response.reasoning_summary_text.done",
        {
            "type": "response.reasoning_summary_text.done",
            "item_id": item_id,
            "output_index": output_index,
            "summary_index": summary_index,
            "text": text,
        },
    )


def reasoning_summary_part_done(item_id: str, output_index: int | None, summary_index: int, part: dict) -> bytes:
    return sse_event(
        "response.reasoning_summary_part.done",
        {
            "type": "response.reasoning_summary_part.done",
            "item_id": item_id,
            "output_index": output_index,
            "summary_index": summary_index,
            "part": part,
        },
    )


def function_arguments_delta(item_id: str, output_index: int | None, delta: str) -> bytes:
    return sse_event(
        "response.function_call_arguments.delta",
        {
            "type": "response.function_call_arguments.delta",
            "item_id": item_id,
            "output_index": output_index,
            "delta": delta,
        },
    )


def function_arguments_done(item_id: str, output_index: int | None, arguments: str) -> bytes:
    return sse_event(
        "response.function_call_arguments.done",
        {
            "type": "response.function_call_arguments.done",
            "item_id": item_id,
            "output_index": output_index,
            "arguments": arguments,
        },
    )


def custom_input_delta(item_id: str, output_index: int | None, delta: str) -> bytes:
    return sse_event(
        "response.custom_tool_call_input.delta",
        {
            "type": "response.custom_tool_call_input.delta",
            "item_id": item_id,
            "output_index": output_index,
            "delta": delta,
        },
    )


def custom_input_done(item_id: str, output_index: int | None, input_text: str) -> bytes:
    return sse_event(
        "response.custom_tool_call_input.done",
        {
            "type": "response.custom_tool_call_input.done",
            "item_id": item_id,
            "output_index": output_index,
            "input": input_text,
        },
    )
