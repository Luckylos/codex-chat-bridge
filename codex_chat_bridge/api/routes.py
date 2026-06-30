from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response as StarletteResponse

from ..bridge_context import BridgeToolContext, build_tool_context_from_request
from ..chat_to_responses import chat_text_to_responses
from ..config import get_settings
from ..errors import BridgeError, InvalidRequestError, UpstreamError
from ..metrics import concurrency_usage
from ..models import ResponsesRequest
from ..protocol.session import _assistant_message_from_chat_body, resolve_session, save_session
from ..responses_to_chat import responses_to_chat_request
from ..stream_chat_to_responses import (
    create_responses_sse_from_chat_response,
    create_responses_sse_stream_from_chat_stream,
)
from ..upstream import UpstreamClient
from .concurrency import _get_semaphore
from .lifespan import create_app
from .policy import validate_effective_messages

_logger = logging.getLogger("codex-chat-bridge")

app = create_app()


def _extract_upstream_error_detail(response: httpx.Response) -> Any:
    try:
        payload = response.json()
    except Exception:
        payload = None

    if isinstance(payload, dict):
        return payload

    text = response.text.strip()
    return text or None


def _extract_upstream_error_message(response: httpx.Response) -> str:
    detail = _extract_upstream_error_detail(response)
    if isinstance(detail, dict):
        error = detail.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message
        message = detail.get("message")
        if isinstance(message, str) and message.strip():
            return message

    if isinstance(detail, str) and detail.strip():
        return detail

    return f"Upstream returned HTTP {response.status_code}"


def _raise_upstream_status_error(exc: httpx.HTTPStatusError, *, code: str) -> None:
    response = exc.response
    raise UpstreamError(
        _extract_upstream_error_message(response),
        code=code,
        status_code=response.status_code,
        detail=_extract_upstream_error_detail(response),
    ) from exc


@app.get("/health")
async def health(request: Request) -> dict:
    return {
        "ok": True,
        "service": "codex-chat-bridge",
        "upstream_reachable": getattr(request.app.state, "health_upstream_reachable", None),
    }


@app.get("/metrics")
async def metrics() -> StarletteResponse:
    return StarletteResponse(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


@app.get("/v1/models")
async def list_models() -> JSONResponse:
    try:
        models = await UpstreamClient(get_settings()).list_models()
        return JSONResponse({"object": "list", "data": models})
    except httpx.HTTPStatusError as exc:
        _raise_upstream_status_error(exc, code="upstream_models_unavailable")
    except BridgeError:
        raise
    except Exception as exc:
        raise UpstreamError(
            str(exc),
            code="upstream_models_unavailable",
            status_code=502,
        ) from exc


@app.post("/v1/responses")
async def create_response(payload: ResponsesRequest):
    return await _create_response_impl(payload)


@app.post("/v1/responses/compact")
async def create_response_compact(payload: ResponsesRequest):
    return await _create_response_impl(payload)


async def _create_response_impl(payload: ResponsesRequest):
    sem = _get_semaphore()
    concurrency_usage.inc()
    try:
        async with sem:
            return await _create_response_core(payload)
    finally:
        concurrency_usage.dec()


async def _create_response_core(payload: ResponsesRequest):
    try:
        resolved_model = (payload.model or "").strip()
        if not resolved_model:
            raise InvalidRequestError(
                "Responses request is missing required field: model.",
                code="missing_model",
            )

        requested_n = payload.n if payload.n is not None else 1
        if requested_n != 1:
            raise InvalidRequestError(
                "Responses requests with n != 1 are not supported by this bridge.",
                code="unsupported_n",
                detail={"n": payload.n},
            )

        settings = get_settings()

        existing_messages, session_context, _session_model = resolve_session(payload)
        if existing_messages is not None:
            tool_context = session_context
        else:
            tool_context = build_tool_context_from_request(payload)

        assert tool_context is not None

        chat_request = responses_to_chat_request(
            payload,
            resolved_model,
            tool_context,
            existing_messages=existing_messages,
        )
        validate_effective_messages(chat_request)

        bridge_id = f"resp_bridge_{uuid.uuid4().hex[:12]}"
        original_request = payload.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
        client = UpstreamClient(settings)

        if payload.stream:
            if settings.upstream_streaming:
                return await _stream_upstream_streaming(
                    client,
                    chat_request,
                    tool_context,
                    bridge_id,
                    original_request=original_request,
                )
            return await _stream_buffer_then_sse(
                client,
                chat_request,
                tool_context,
                bridge_id,
                original_request=original_request,
            )

        chat_body = await client.create_chat_completion(chat_request)
        response_body = chat_text_to_responses(
            chat_body,
            chat_request.model,
            tool_context,
            original_request=original_request,
        )
        assistant_message = _assistant_message_from_chat_body(chat_body)
        raw = response_body.model_dump(mode="json")
        raw["id"] = bridge_id
        save_session(
            bridge_id,
            chat_request.messages,
            tool_context,
            chat_request.model,
            assistant_message=assistant_message,
        )
        return JSONResponse(raw)

    except BridgeError:
        raise
    except httpx.HTTPStatusError as exc:
        _raise_upstream_status_error(exc, code="upstream_request_failed")
    except Exception as exc:
        raise BridgeError(str(exc), code="bridge_runtime_error", status_code=500) from exc


async def _stream_upstream_streaming(
    client: UpstreamClient,
    chat_request,
    tool_context: BridgeToolContext,
    bridge_id: str,
    original_request: dict | None = None,
) -> StreamingResponse:
    """Stream: upstream supports streaming → passthrough SSE with session save."""
    captured: list = []
    raw_stream = create_responses_sse_stream_from_chat_stream(
        client.stream_chat_completion(chat_request),
        tool_context,
        response_id=bridge_id,
        original_request=original_request,
        _captured_state=captured,
    )

    async def _yield_and_save() -> AsyncIterator[bytes]:
        saw_output = False
        async for chunk in raw_stream:
            saw_output = True
            yield chunk
        assistant_message = captured[0].build_assistant_message() if captured else None
        if saw_output:
            save_session(
                bridge_id,
                chat_request.messages,
                tool_context,
                chat_request.model,
                assistant_message=assistant_message,
            )

    return StreamingResponse(_yield_and_save(), media_type="text/event-stream")


async def _stream_buffer_then_sse(
    client: UpstreamClient,
    chat_request,
    tool_context: BridgeToolContext,
    bridge_id: str,
    original_request: dict | None = None,
) -> StreamingResponse:
    """Stream: upstream doesn't stream → buffer chat_body, wrap as SSE, save session."""
    chat_body = await client.create_chat_completion(chat_request)
    raw_stream = create_responses_sse_from_chat_response(
        chat_body,
        tool_context,
        response_id=bridge_id,
        original_request=original_request,
    )
    assistant_message = _assistant_message_from_chat_body(chat_body)

    async def _yield_and_save() -> AsyncIterator[bytes]:
        async for chunk in raw_stream:
            yield chunk
        save_session(
            bridge_id,
            chat_request.messages,
            tool_context,
            chat_request.model,
            assistant_message=assistant_message,
        )

    return StreamingResponse(_yield_and_save(), media_type="text/event-stream")
