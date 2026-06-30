from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from ..config import get_settings, validate_config
from ..errors import BridgeError
from ..upstream import UpstreamClient
from .errors import bridge_error_response
from .middleware import RequestLogMiddleware

_logger = logging.getLogger("codex-chat-bridge")
_access_logger = logging.getLogger("codex-chat-bridge.access")

HEALTH_UPSTREAM_REACHABLE_STATE_KEY = "health_upstream_reachable"


def _http_exception_message(detail: Any, status_code: int) -> str:
    if isinstance(detail, str) and detail.strip():
        return detail
    if isinstance(detail, dict):
        error = detail.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message
        message = detail.get("message")
        if isinstance(message, str) and message.strip():
            return message
    return f"HTTP {status_code}"


async def handle_bridge_error(_request: Request, exc: Exception):
    assert isinstance(exc, BridgeError)
    return bridge_error_response(exc)


async def handle_http_exception(_request: Request, exc: Exception):
    assert isinstance(exc, HTTPException)
    normalized = BridgeError(
        _http_exception_message(exc.detail, exc.status_code),
        code="http_exception",
        status_code=exc.status_code,
        detail=exc.detail,
    )
    return bridge_error_response(normalized)


@asynccontextmanager
async def bridge_lifespan(app: FastAPI):
    """Startup: configure logging, validate config, check upstream. Shutdown."""
    app.state.health_upstream_reachable = None

    if not _access_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        _access_logger.addHandler(handler)
        _access_logger.setLevel(logging.INFO)

    try:
        validate_config()
    except RuntimeError as exc:
        _logger.error("Startup config validation failed: %s", exc)
        raise

    try:
        models = await UpstreamClient(get_settings()).list_models()
        app.state.health_upstream_reachable = True
        _logger.info("Upstream connectivity: ok (%d upstream models)", len(models))
    except Exception as exc:
        app.state.health_upstream_reachable = False
        _logger.warning("Upstream connectivity check failed: %s", exc)

    yield
    _logger.info("Shutdown complete.")


def create_app() -> FastAPI:
    """Build and wire the FastAPI application."""
    application = FastAPI(
        title="codex-chat-bridge",
        version="0.4.0",
        lifespan=bridge_lifespan,
    )
    application.add_middleware(RequestLogMiddleware)
    application.add_exception_handler(BridgeError, handle_bridge_error)
    application.add_exception_handler(HTTPException, handle_http_exception)
    return application
