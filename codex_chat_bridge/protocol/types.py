"""Typed definitions for protocol-layer data flowing through the bridge.

These TypedDicts provide static type-safety for the conversion hot-path
without changing runtime behavior — dict operations continue to work
exactly as before.  They are purely for static analysis.

Usage: import from this module instead of writing `dict[str, Any]`.
"""
from __future__ import annotations

from typing import Any, Literal, TypedDict


# ---- Chat Completions message (input to Chat→Responses conversion) ----

class ChatMessageInput(TypedDict, total=False):
    """Shape of a Chat Completions message as received from upstream.

    Used by chat_to_responses extraction functions.
    """
    role: str
    content: str | list[dict[str, Any]]
    reasoning_content: str
    reasoning: str
    tool_calls: list[dict[str, Any]]
    refusal: str
    annotations: list[dict[str, Any]]
    function: dict[str, Any]
    tool_call_id: str


class ChatResponseInput(TypedDict, total=False):
    """Shape of a full Chat Completions response body (non-streaming)."""
    id: str
    object: str
    model: str
    created: int
    choices: list[dict[str, Any]]
    usage: dict[str, Any]
    error: dict[str, Any]


# ---- Responses API input items (input to Responses→Chat conversion) ----

class ResponsesInputItem(TypedDict, total=False):
    """Shape of a Responses API input item dict.

    Covers function_call, function_call_output, reasoning,
    input_text, input_image, input_audio, message, etc.
    """
    type: str
    call_id: str
    id: str
    name: str
    namespace: str
    arguments: str
    input: str
    output: Any
    text: str
    role: str
    content: Any
    tool_calls: list[dict[str, Any]]
    tool_call_id: str
    reasoning_content: str
    summary: list[dict[str, Any]]
    status: str


# ---- Chat Completions tool call (output product) ----

class ChatToolCallOutput(TypedDict, total=False):
    """Shape of a Chat Completions tool_call object produced by the bridge."""
    id: str
    type: Literal["function"]
    function: dict[str, Any]


# ---- Content parts (shared between both directions) ----

class TextPart(TypedDict, total=False):
    """A text content part."""
    type: Literal["text", "output_text"]
    text: str
    annotations: list[dict[str, Any]]


class RefusalPart(TypedDict, total=False):
    """A refusal content part."""
    type: Literal["refusal"]
    refusal: str


class ImageURLPart(TypedDict, total=False):
    """An image_url content part."""
    type: Literal["image_url"]
    image_url: dict[str, Any]


class InputAudioPart(TypedDict, total=False):
    """An input_audio content part."""
    type: Literal["input_audio"]
    input_audio: dict[str, Any]


ContentPart = TextPart | RefusalPart | ImageURLPart | InputAudioPart
