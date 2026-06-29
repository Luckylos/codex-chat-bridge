"""Shared protocol types for the codex-chat-bridge.

These TypedDict and type-alias definitions capture the shape of
data flowing through the bridge's critical paths — upstream Chat
Completions chunks, tool call deltas, SSE event payloads, etc.

They are informational (runtime is still dict-based) but provide
static type-safety for the conversion hot-path.
"""
from __future__ import annotations

from typing import Any, Literal, TypedDict


# ---- Chat Completions upstream response types ----

class ChatUsage(TypedDict, total=False):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    input_tokens: int
    output_tokens: int
    completion_tokens_details: dict[str, Any]
    prompt_tokens_details: dict[str, Any]


class ChatToolCallFunction(TypedDict, total=False):
    name: str
    arguments: str


class ChatToolCall(TypedDict, total=False):
    id: str
    call_id: str
    type: str
    index: int
    function: ChatToolCallFunction


class ChatDelta(TypedDict, total=False):
    role: str
    content: str | list[dict[str, Any]]
    reasoning_content: str
    reasoning: str
    tool_calls: list[ChatToolCall]
    refusal: str


class ChatChoice(TypedDict, total=False):
    index: int
    delta: ChatDelta
    message: dict[str, Any]
    finish_reason: str


class ChatChunk(TypedDict, total=False):
    id: str
    object: str
    model: str
    created: int
    choices: list[ChatChoice]
    usage: ChatUsage
    error: dict[str, Any]


# ---- Responses API output types ----

class OutputTextPart(TypedDict, total=False):
    type: Literal["output_text"]
    text: str
    annotations: list[dict[str, Any]]


class RefusalPart(TypedDict, total=False):
    type: Literal["refusal"]
    refusal: str


ContentPart = OutputTextPart | RefusalPart


class ReasoningSummaryEntry(TypedDict, total=False):
    type: Literal["summary_text"]
    text: str


# ---- Usage mapping result ----

class ResponsesUsage(TypedDict, total=False):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    input_tokens_details: dict[str, Any]
    output_tokens_details: dict[str, Any]


# ---- Incomplete details ----

class IncompleteDetails(TypedDict):
    reason: str
