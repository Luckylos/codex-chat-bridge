from __future__ import annotations

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response as StarletteResponse

from ..config import get_settings
from ..errors import BridgeError, UpstreamError
from ..metrics import concurrency_usage
from ..models import ResponsesRequest
from ..protocol.session import resolve_session, save_session
from ..upstream import UpstreamClient
from .concurrency import _get_semaphore
from .lifespan import create_app
from .response_service import (
    create_response_core,
    extract_upstream_error_detail,
    extract_upstream_error_message,
    raise_upstream_status_error,
    stream_buffer_then_sse,
    stream_upstream_streaming,
)

app = create_app()

_extract_upstream_error_detail = extract_upstream_error_detail
_extract_upstream_error_message = extract_upstream_error_message
_raise_upstream_status_error = raise_upstream_status_error
_stream_buffer_then_sse = stream_buffer_then_sse
_stream_upstream_streaming = stream_upstream_streaming


@app.get("/health")
async def health(request: Request) -> dict:
    return {
        "ok": True,
        "service": "codex-chat-bridge",
        "upstream_reachable": getattr(request.app.state, "health_upstream_reachable", None),
    }


@app.get("/metrics")
async def metrics() -> StarletteResponse:
    return StarletteResponse(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


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
        raise UpstreamError(str(exc), code="upstream_models_unavailable", status_code=502) from exc


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
    return await create_response_core(
        payload,
        get_settings_fn=get_settings,
        upstream_client_cls=UpstreamClient,
        resolve_session_fn=resolve_session,
        save_session_fn=save_session,
        raise_upstream_status_error_fn=_raise_upstream_status_error,
        stream_upstream_streaming_fn=_stream_upstream_streaming,
        stream_buffer_then_sse_fn=_stream_buffer_then_sse,
    )
