"""Protocol subpackage — SSE serialization, protocol types, and session storage.

Groups the protocol-layer concerns together: how the bridge speaks SSE,
how it models request/response data, and how it tracks sessions.
"""
from .sse import (
    extract_block,
    parse_sse_block,
    parse_sse_json_block,
    serialize_event,
    sse_event,
    sse_done,
    iter_sse_bytes_as_list,
)

__all__ = [
    "extract_block",
    "parse_sse_block",
    "parse_sse_json_block",
    "serialize_event",
    "sse_event",
    "sse_done",
    "iter_sse_bytes_as_list",
]
