import httpx
import uuid
from collections.abc import AsyncIterator
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from ..bridge_context import BridgeToolContext, build_tool_context_from_request
from ..chat_to_responses import chat_text_to_responses
from ..config import get_settings
from ..models import ChatMessage, ResponsesRequest
from ..session_store import _assistant_message_from_chat_body, resolve_session, save_session
from ..stream_chat_to_responses import (
    create_responses_sse_from_chat_response,
    create_responses_sse_stream_from_chat_stream,
)
from ..transform_responses_to_chat import UnsupportedResponsesInputItemError, responses_to_chat_request
from ..upstream import UpstreamClient
from .errors import build_error_response, invalid_request_error
from .policy import validate_effective_messages


app = FastAPI(title="codex-chat-bridge", version="0.3.0")


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

        # ---- previous_response_id 恢复 ----
        existing_messages, session_context, session_model = resolve_session(payload)
        if existing_messages is not None:
            # 使用会话中的 model 作为 fallback
            if not resolved_model and session_model:
                resolved_model = session_model
            tool_context = session_context or build_tool_context_from_request(payload)
        else:
            tool_context = build_tool_context_from_request(payload)

        chat_request = responses_to_chat_request(
            payload, resolved_model, tool_context,
            existing_messages=existing_messages,
        )
        policy_error = validate_effective_messages(chat_request)
        if policy_error is not None:
            return policy_error

        # ---- 为本次响应生成 bridge 级 response_id，用于 session 索引 ----
        bridge_id = f"resp_bridge_{uuid.uuid4().hex[:12]}"

        client = UpstreamClient(settings)
        if payload.stream:
            if settings.upstream_streaming:
                captured: list = []
                raw_stream = create_responses_sse_stream_from_chat_stream(
                    client.stream_chat_completion(chat_request),
                    tool_context,
                    response_id=bridge_id,
                    _captured_state=captured,
                )

                async def _save_when_done() -> AsyncIterator[bytes]:
                    saw_output = False
                    async for chunk in raw_stream:
                        saw_output = True
                        yield chunk
                    _assistant = captured[0].build_assistant_message() if captured else None
                    if saw_output:
                        save_session(bridge_id, chat_request.messages, tool_context, chat_request.model,
                                     assistant_message=_assistant)

                return StreamingResponse(_save_when_done(), media_type="text/event-stream")

            # upstream_streaming=False, payload.stream=True: 先把 chat_body 拉回来再包装 SSE
            chat_body = await client.create_chat_completion(chat_request)
            raw_stream = create_responses_sse_from_chat_response(
                chat_body, tool_context, response_id=bridge_id,
            )
            _assistant = _assistant_message_from_chat_body(chat_body)

            async def _save_when_done() -> AsyncIterator[bytes]:
                saw_output = False
                async for chunk in raw_stream:
                    saw_output = True
                    yield chunk
                if saw_output:
                    save_session(bridge_id, chat_request.messages, tool_context, chat_request.model,
                                 assistant_message=_assistant)  # type: ignore[possibly-undefined]

            return StreamingResponse(_save_when_done(), media_type="text/event-stream")

        chat_body = await client.create_chat_completion(chat_request)
        response_body = chat_text_to_responses(chat_body, chat_request.model, tool_context)
        _assistant = _assistant_message_from_chat_body(chat_body)
        raw = response_body.model_dump(mode="json")
        raw["id"] = bridge_id
        save_session(bridge_id, chat_request.messages, tool_context, chat_request.model,
                     assistant_message=_assistant)
        return JSONResponse(raw)
    except UnsupportedResponsesInputItemError as exc:
        return invalid_request_error(str(exc), "unsupported_input_item")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc
    except Exception as exc:
        return build_error_response(str(exc), code="bridge_runtime_error", status_code=500)
