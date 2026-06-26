from __future__ import annotations

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from ..bridge_context import build_tool_context_from_request
from ..chat_to_responses import chat_text_to_responses
from ..config import get_settings
from ..models import ResponsesRequest
from ..stream_chat_to_responses import (
    create_responses_sse_from_chat_response,
    create_responses_sse_stream_from_chat_stream,
)
from ..transform_responses_to_chat import UnsupportedResponsesInputItemError, responses_to_chat_request
from ..upstream import UpstreamClient
from .errors import build_error_response, invalid_request_error
from .policy import validate_effective_messages

app = FastAPI(title="codex-chat-bridge", version="0.2.0")


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "service": "codex-chat-bridge"}


@app.get("/v1/models")
async def list_models() -> JSONResponse:
    try:
        models = await UpstreamClient(get_settings()).list_models()
        return JSONResponse({"object": "list", "data": models})
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc
    except Exception as exc:
        return build_error_response(str(exc), code="upstream_models_unavailable", status_code=502)


@app.post("/v1/responses")
async def create_response(payload: ResponsesRequest):
    return await _create_response_impl(payload)


@app.post("/v1/responses/compact")
async def create_response_compact(payload: ResponsesRequest):
    return await _create_response_impl(payload)


async def _create_response_impl(payload: ResponsesRequest):
    try:
        settings = get_settings()
        resolved_model = (payload.model or "").strip()
        if not resolved_model:
            return invalid_request_error(
                "Responses request is missing required field: model.",
                "missing_model",
            )

        tool_context = build_tool_context_from_request(payload)
        chat_request = responses_to_chat_request(payload, resolved_model, tool_context)
        policy_error = validate_effective_messages(chat_request)
        if policy_error is not None:
            return policy_error

        client = UpstreamClient(settings)
        if payload.stream:
            if settings.upstream_streaming:
                stream = create_responses_sse_stream_from_chat_stream(
                    client.stream_chat_completion(chat_request),
                    tool_context,
                )
            else:
                chat_body = await client.create_chat_completion(chat_request)
                stream = create_responses_sse_from_chat_response(chat_body, tool_context)
            return StreamingResponse(stream, media_type="text/event-stream")

        chat_body = await client.create_chat_completion(chat_request)
        response_body = chat_text_to_responses(chat_body, chat_request.model, tool_context)
        return JSONResponse(response_body.model_dump(mode="json"))
    except UnsupportedResponsesInputItemError as exc:
        return invalid_request_error(str(exc), "unsupported_input_item")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc
    except Exception as exc:
        return build_error_response(str(exc), code="bridge_runtime_error", status_code=500)
