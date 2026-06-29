from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from ..config import get_settings
from ..errors import BridgeError
from ..upstream import UpstreamClient
from .errors import bridge_error_response
from .middleware import RequestLogMiddleware

_logger = logging.getLogger("codex-chat-bridge")
_access_logger = logging.getLogger("codex-chat-bridge.access")

health_upstream_reachable: bool | None = None


async def handle_bridge_error(_request: Request, exc: Exception):
    assert isinstance(exc, BridgeError)
    return bridge_error_response(exc)


@asynccontextmanager
async def bridge_lifespan(_app: FastAPI):
    """Startup: configure logging, validate config, check upstream. Shutdown."""
    global health_upstream_reachable

    # Ensure access logger has a handler if none configured
    if not _access_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        _access_logger.addHandler(handler)
        _access_logger.setLevel(logging.INFO)

    try:
        from ..app import validate_config
        validate_config()
    except RuntimeError as exc:
        _logger.error("Startup config validation failed: %s", exc)
        raise
    try:
        models = await UpstreamClient(get_settings()).list_models()
        health_upstream_reachable = True
        _logger.info("Upstream connectivity: ok (%d upstream models)", len(models))
    except Exception as exc:
        health_upstream_reachable = False
        _logger.warning("Upstream connectivity check failed: %s", exc)

    yield
    _logger.info("Shutdown complete.")


def create_app() -> FastAPI:
    """Build and wire the FastAPI application."""
    application = FastAPI(
        title="codex-chat-bridge", version="0.4.0", lifespan=bridge_lifespan,
    )
    application.add_middleware(RequestLogMiddleware)
    application.add_exception_handler(BridgeError, handle_bridge_error)
    return application
