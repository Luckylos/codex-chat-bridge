from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ResponsesInputText(BaseModel):
    type: Literal["input_text"] = "input_text"
    text: str


class ResponsesMessage(BaseModel):
    role: Literal["user", "assistant", "system", "developer", "tool"]
    content: Any | None = None


class ResponsesRequest(BaseModel):
    model: str | None = None
    previous_response_id: str | None = None
    input: Any | None = None
    instructions: Any | None = None
    stream: bool = False
    stream_options: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None
    text: dict[str, Any] | None = None
    response_format: Any = None
    metadata: dict[str, Any] | None = None
    n: int | None = None
    parallel_tool_calls: bool | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    seed: int | None = None
    service_tier: str | None = None
    stop: str | list[str] | None = None
    user: str | None = None
    logit_bias: dict[str, Any] | None = None
    logprobs: bool | int | None = None
    top_logprobs: int | None = None
    reasoning: dict[str, Any] | None = None
    max_output_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]] | None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    reasoning_content: str | None = None


class ChatCompletionsRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    stream: bool = False
    stream_options: dict[str, Any] | None = None
    thinking: dict[str, Any] | None = None
    reasoning_effort: str | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None
    response_format: Any = None
    metadata: dict[str, Any] | None = None
    n: int | None = None
    parallel_tool_calls: bool | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    seed: int | None = None
    service_tier: str | None = None
    stop: str | list[str] | None = None
    user: str | None = None
    logit_bias: dict[str, Any] | None = None
    logprobs: bool | int | None = None
    top_logprobs: int | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None


class ModelCard(BaseModel):
    id: str
    object: Literal["model"] = "model"
    owned_by: str = "codex-chat-bridge"


class ModelsResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[ModelCard]


class ErrorBody(BaseModel):
    message: str
    type: str = "bridge_error"
    code: str = "bridge_error"
    param: str | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class ResponsesOutputText(BaseModel):
    type: Literal["output_text"] = "output_text"
    text: str
    annotations: list[Any] = Field(default_factory=list)


class ResponsesResponse(BaseModel):
    id: str
    object: Literal["response"] = "response"
    status: str = "completed"
    model: str
    output: list[dict[str, Any]]
    output_text: str
    created_at: int | None = None
    usage: dict[str, Any] | None = None
    incomplete_details: dict[str, Any] | None = None
    # Request-echo fields (part of OpenAI Responses response object spec)
    instructions: Any | None = None
    max_output_tokens: int | None = None
    parallel_tool_calls: bool | None = None
    previous_response_id: str | None = None
    reasoning: dict[str, Any] | None = None
    temperature: float | None = None
    tool_choice: Any = None
    tools: list[dict[str, Any]] | None = None
    top_p: float | None = None
    metadata: dict[str, Any] | None = None
