from __future__ import annotations

from .envelope import sse_event


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
