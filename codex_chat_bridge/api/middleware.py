from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from ..metrics import request_duration_ms, requests_in_flight, requests_total

_logger = logging.getLogger("codex-chat-bridge.access")


class RequestLogMiddleware(BaseHTTPMiddleware):
    """Middleware that logs request metadata and records Prometheus metrics."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start = time.monotonic()
        method = request.method
        path = request.url.path
        error: str | None = None

        requests_in_flight.inc()

        try:
            response = await call_next(request)
            status = response.status_code
        except Exception as exc:
            status = 500
            error = str(exc)
            raise
        finally:
            requests_in_flight.dec()
            duration_ms = round((time.monotonic() - start) * 1000)
            request_duration_ms.labels(method=method, path=path).observe(duration_ms)
            requests_total.labels(method=method, path=path, status=str(status)).inc()

            # Access log (stdout JSONL)
            record = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(start)),
                "method": method,
                "path": path,
                "status": status,
                "duration_ms": duration_ms,
            }
            if error:
                record["error"] = error
            _logger.info(json.dumps(record, ensure_ascii=False))

        return response
