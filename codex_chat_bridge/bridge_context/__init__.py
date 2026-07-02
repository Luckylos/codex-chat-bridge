"""Request-scoped bridge context package.

Centralizes tool-family constants, namespace-name flattening, custom-tool
argument helpers, tool-search discovery, and per-request tool context
construction so request transform, buffered restore, and streaming restore
share one source of truth.
"""

from .builder import build_tool_context_from_request, collect_tool_search_output_tools, iter_request_input_items
from .constants import CHAT_TOOL_NAME_MAX_LEN, CUSTOM_TOOL_INPUT_FIELD, TOOL_SEARCH_PROXY_NAME
from .context import BridgeToolContext
from .custom_tools import custom_tool_input_from_chat_arguments, custom_tool_input_to_chat_arguments, parse_tool_arguments_object
from .models import ToolSpec
from .naming import canonical_json_string, flatten_namespace_tool_name
from .nested_namespace import NestedNamespaceResolution, resolve_nested_namespace_arguments

__all__ = [
    "BridgeToolContext",
    "CHAT_TOOL_NAME_MAX_LEN",
    "CUSTOM_TOOL_INPUT_FIELD",
    "TOOL_SEARCH_PROXY_NAME",
    "ToolSpec",
    "build_tool_context_from_request",
    "canonical_json_string",
    "collect_tool_search_output_tools",
    "custom_tool_input_from_chat_arguments",
    "custom_tool_input_to_chat_arguments",
    "flatten_namespace_tool_name",
    "iter_request_input_items",
    "NestedNamespaceResolution",
    "parse_tool_arguments_object",
    "resolve_nested_namespace_arguments",
]
