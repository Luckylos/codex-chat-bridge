"""Bridge error → JSONResponse conversion.

Used by the BridgeError exception handler registered in lifespan.py.
All bridge errors now flow through BridgeError raise → exception_handler.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi.responses import JSONResponse

from ..errors import BridgeError
from ..models import ErrorBody, ErrorEnvelope


def _error_param(detail: Any) -> str | None:
    if detail is None:
        return None
    if isinstance(detail, str):
        return detail
    try:
        return json.dumps(detail, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except TypeError:
        return str(detail)


def bridge_error_response(exc: BridgeError) -> JSONResponse:
    """Build a JSONResponse from a BridgeError exception.

    Provides a unified path for all raised bridge errors.
    """
    envelope = ErrorEnvelope(
        error=ErrorBody(
            message=exc.message,
            type=exc.error_type,
            code=exc.code,
            param=_error_param(exc.detail),
        )
    )
    return JSONResponse(status_code=exc.status_code, content=envelope.model_dump(mode="json", exclude_none=True))
