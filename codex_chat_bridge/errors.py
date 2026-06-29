"""Unified bridge error types.

Both non-streaming and streaming error paths share these types
so that error construction, logging, and propagation are consistent
across the bridge.
"""
from __future__ import annotations

from typing import Any


class BridgeError(Exception):
    """Base class for all bridge errors.

    Carries a machine-readable code, a human-readable message,
    and an optional HTTP status code.  Non-streaming handlers
    raise this; streaming handlers catch and emit SSE error events.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "bridge_error",
        error_type: str = "bridge_error",
        status_code: int = 500,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.error_type = error_type
        self.status_code = status_code
        self.detail = detail

    def to_error_body(self) -> dict[str, Any]:
        """Convert to the OpenAI-style error body dict."""
        body: dict[str, Any] = {
            "message": self.message,
            "type": self.error_type,
            "code": self.code,
        }
        if self.detail is not None:
            body["detail"] = self.detail
        return body

    def to_error_envelope(self) -> dict[str, Any]:
        """Full error envelope: {\"error\": {...}}."""
        return {"error": self.to_error_body()}


class InvalidRequestError(BridgeError):
    """400-class error for malformed or unsupported requests."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "invalid_request",
        detail: Any = None,
    ) -> None:
        super().__init__(
            message,
            code=code,
            error_type="invalid_request_error",
            status_code=400,
            detail=detail,
        )


class UpstreamError(BridgeError):
    """Error from the upstream Chat Completions provider."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "upstream_error",
        status_code: int = 502,
        detail: Any = None,
    ) -> None:
        super().__init__(
            message,
            code=code,
            error_type="upstream_error",
            status_code=status_code,
            detail=detail,
        )


class StreamError(BridgeError):
    """Error during streaming — emitted as SSE response.failed event."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "stream_error",
        detail: Any = None,
    ) -> None:
        super().__init__(
            message,
            code=code,
            error_type="stream_error",
            status_code=500,
            detail=detail,
        )


class UnsupportedInputItemError(InvalidRequestError):
    """An input item type or value is not supported by the bridge."""

    def __init__(
        self,
        message: str,
        *,
        item_type: str | None = None,
        detail: Any = None,
    ) -> None:
        super().__init__(
            message,
            code="unsupported_input_item",
            detail=detail,
        )
        self.item_type = item_type
