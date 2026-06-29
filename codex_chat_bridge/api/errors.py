from __future__ import annotations

from fastapi.responses import JSONResponse

from ..errors import BridgeError
from ..models import ErrorBody, ErrorEnvelope


def build_error_response(
    message: str,
    *,
    code: str,
    error_type: str = "bridge_error",
    status_code: int = 400,
) -> JSONResponse:
    envelope = ErrorEnvelope(
        error=ErrorBody(
            message=message,
            type=error_type,
            code=code,
        )
    )
    return JSONResponse(status_code=status_code, content=envelope.model_dump(mode="json"))


def invalid_request_error(message: str, code: str) -> JSONResponse:
    return build_error_response(message, code=code, error_type="invalid_request_error", status_code=400)


def bridge_error_response(exc: BridgeError) -> JSONResponse:
    """Build a JSONResponse from a BridgeError exception.

    Provides a unified path for both raised and explicitly-constructed errors.
    """
    envelope = ErrorEnvelope(
        error=ErrorBody(
            message=exc.message,
            type=exc.error_type,
            code=exc.code,
        )
    )
    return JSONResponse(status_code=exc.status_code, content=envelope.model_dump(mode="json"))
