from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
from fastapi.responses import JSONResponse, StreamingResponse

from ..bridge_context import BridgeToolContext, build_tool_context_from_request
from ..chat_to_responses import chat_text_to_responses
from ..config import get_settings
from ..errors import BridgeError, InvalidRequestError, UpstreamError
from ..metrics import upstream_errors_total
from ..protocol.session import _assistant_message_from_chat_body, resolve_session, save_session
from ..responses_to_chat import responses_to_chat_request
from ..stream_chat_to_responses import (
    create_responses_sse_from_chat_response,
    create_responses_sse_stream_from_chat_stream,
)
from ..upstream import UpstreamClient
from .policy import validate_effective_messages

_logger = logging.getLogger("codex-chat-bridge")


def extract_upstream_error_detail(response: httpx.Response) -> Any:
    try:
        payload = response.json()
    except Exception:
        payload = None

    if isinstance(payload, dict):
        return payload

    text = response.text.strip()
    return text or None


def extract_upstream_error_message(response: httpx.Response) -> str:
    detail = extract_upstream_error_detail(response)
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


def _record_upstream_error(model: str | None, status_code: int | str) -> None:
    upstream_errors_total.labels(model=(model or "unknown"), status_code=str(status_code)).inc()


def raise_upstream_status_error(exc: httpx.HTTPStatusError, *, code: str, model: str | None = None) -> None:
    response = exc.response
    _record_upstream_error(model, response.status_code)
    raise UpstreamError(
        extract_upstream_error_message(response),
        code=code,
        status_code=response.status_code,
        detail=extract_upstream_error_detail(response),
    ) from exc


@dataclass(slots=True)
class ServiceDependencies:
    """Typed dependency bundle for the response service layer.

    All fields default to the production implementations; tests can
    override individual fields without patching module globals.
    """

    get_settings: Callable[[], Any] = field(default_factory=lambda: get_settings)
    upstream_client_cls: type[Any] = field(default_factory=lambda: UpstreamClient)
    resolve_session: Callable[..., Any] = field(default_factory=lambda: resolve_session)
    save_session: Callable[..., Any] = field(default_factory=lambda: save_session)
    raise_upstream_status_error: Callable[..., Any] = field(default_factory=lambda: raise_upstream_status_error)


async def create_response_core(
    payload,
    *,
    deps: ServiceDependencies | None = None,
):
    deps = deps or ServiceDependencies()
    try:
        resolved_model = (payload.model or "").strip()

        requested_n = payload.n if payload.n is not None else 1
        if requested_n != 1:
            raise InvalidRequestError(
                "Responses requests with n != 1 are not supported by this bridge.",
                code="unsupported_n",
                detail={"n": payload.n},
            )

        settings = deps.get_settings()
        existing_messages, session_context, session_model = deps.resolve_session(payload)
        session_model = (session_model or "").strip()
        if not resolved_model and session_model:
            resolved_model = session_model
        elif resolved_model and session_model and resolved_model != session_model:
            _logger.debug(
                "Responses session model changed: previous_response_id=%s session_model=%s requested_model=%s",
                payload.previous_response_id,
                session_model,
                resolved_model,
            )
        if not resolved_model:
            raise InvalidRequestError(
                "Responses request is missing required field: model.",
                code="missing_model",
            )
        tool_context = session_context if existing_messages is not None else build_tool_context_from_request(payload)
        if tool_context is None:
            raise InvalidRequestError("No tool context could be resolved for the request.", code="no_tool_context")

        chat_request = responses_to_chat_request(
            payload,
            resolved_model,
            tool_context,
            existing_messages=existing_messages,
        )
        validate_effective_messages(chat_request)

        bridge_id = f"resp_bridge_{uuid.uuid4().hex[:12]}"
        original_request = payload.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
        client = deps.upstream_client_cls(settings)

        if payload.stream:
            if settings.upstream_streaming:
                return await _stream_upstream_streaming(
                    client,
                    chat_request,
                    tool_context,
                    bridge_id,
                    deps=deps,
                    original_request=original_request,
                )
            return await _stream_buffer_then_sse(
                client,
                chat_request,
                tool_context,
                bridge_id,
                deps=deps,
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
        deps.save_session(
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
        deps.raise_upstream_status_error(exc, code="upstream_request_failed", model=resolved_model)
    except Exception as exc:
        raise BridgeError(str(exc), code="bridge_runtime_error", status_code=500) from exc


async def _stream_upstream_streaming(
    client: UpstreamClient,
    chat_request,
    tool_context: BridgeToolContext,
    bridge_id: str,
    *,
    deps: ServiceDependencies,
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
        state = captured[0] if captured else None
        assistant_message = state.build_assistant_message() if state is not None else None
        should_save = (
            saw_output
            and state is not None
            and state.envelope.completed
            and state.envelope.status != "failed"
        )
        if should_save:
            deps.save_session(
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
    *,
    deps: ServiceDependencies,
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
        if assistant_message is not None:
            deps.save_session(
                bridge_id,
                chat_request.messages,
                tool_context,
                chat_request.model,
                assistant_message=assistant_message,
            )

    return StreamingResponse(_yield_and_save(), media_type="text/event-stream")
