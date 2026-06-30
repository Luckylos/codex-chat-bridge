from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..config import get_settings
from ..errors import UnsupportedInputItemError
from .constants import CUSTOM_TOOL_INPUT_FIELD, TOOL_SEARCH_PROXY_NAME
from .models import ToolSpec
from .naming import flatten_namespace_tool_name, tool_name_from_value

_logger = logging.getLogger("codex-chat-bridge")
_HOSTED_TOOL_TYPES = frozenset({"web_search", "file_search", "computer_use", "code_interpreter", "mcp"})


@dataclass(slots=True)
class BridgeToolContext:
    custom_tool_names: set[str] = field(default_factory=set)
    tool_search_enabled: bool = False
    chat_name_to_spec: dict[str, ToolSpec] = field(default_factory=dict)
    chat_tools: list[dict[str, Any]] = field(default_factory=list)
    _seen_chat_names: set[str] = field(default_factory=set)
    _namespace_name_to_chat_name: dict[tuple[str, str], str] = field(default_factory=dict)

    def is_custom_tool(self, chat_name: str | None) -> bool:
        return bool(chat_name) and chat_name in self.custom_tool_names

    def is_tool_search(self, chat_name: str | None) -> bool:
        return bool(chat_name) and chat_name == TOOL_SEARCH_PROXY_NAME and self.tool_search_enabled

    def lookup_chat_name(self, chat_name: str | None) -> ToolSpec | None:
        if not chat_name:
            return None
        return self.chat_name_to_spec.get(chat_name)

    def chat_name_for_function(self, name: str, namespace: str | None = None) -> str:
        if namespace and (namespace, name) in self._namespace_name_to_chat_name:
            return self._namespace_name_to_chat_name[(namespace, name)]
        if namespace:
            return flatten_namespace_tool_name(namespace, name)
        return name

    def restore_namespace_and_name(self, chat_name: str) -> tuple[str | None, str]:
        """Given a chat-side flattened tool name, recover the original (namespace, name).

        Returns (namespace, name) where namespace is None for non-namespaced tools.
        Used when translating tool_calls from Chat back to Responses format.
        """
        spec = self.chat_name_to_spec.get(chat_name)
        if spec is not None:
            return spec.namespace, spec.name
        # Fallback: try to split on last __ separator
        if "__" in chat_name:
            ns, _, n = chat_name.rpartition("__")
            if ns and n:
                return ns, n
        return None, chat_name

    def add_chat_tool(self, chat_name: str, spec: ToolSpec, chat_tool: dict[str, Any]) -> None:
        if not chat_name.strip() or chat_name in self._seen_chat_names:
            return
        self._seen_chat_names.add(chat_name)
        self.chat_name_to_spec[chat_name] = spec
        if spec.namespace:
            self._namespace_name_to_chat_name[(spec.namespace, spec.name)] = chat_name
        self.chat_tools.append(chat_tool)
        if spec.kind == "custom":
            self.custom_tool_names.add(chat_name)
        if spec.kind == "tool_search":
            self.tool_search_enabled = True

    def merge(self, other: BridgeToolContext) -> None:
        for chat_tool in other.chat_tools:
            if isinstance(chat_tool, dict) and chat_tool.get("type") in _HOSTED_TOOL_TYPES:
                if chat_tool not in self.chat_tools:
                    self.chat_tools.append(chat_tool)
                continue
            function = chat_tool.get("function") if isinstance(chat_tool, dict) else None
            chat_name = function.get("name") if isinstance(function, dict) else None
            if not isinstance(chat_name, str) or not chat_name.strip():
                continue
            spec = other.chat_name_to_spec.get(chat_name)
            if spec is None:
                continue
            self.add_chat_tool(chat_name, spec, chat_tool)

        for name in other.custom_tool_names:
            self.custom_tool_names.add(name)
            spec = other.chat_name_to_spec.get(name)
            if spec is not None:
                self.chat_name_to_spec.setdefault(name, spec)

        if other.tool_search_enabled:
            self.add_tool_search_tool()

    def add_function_tool(self, tool: dict[str, Any], namespace: str | None = None) -> None:
        function = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = tool_name_from_value(function)
        if not name:
            return
        chat_name = self.chat_name_for_function(name, namespace)
        chat_tool = {
            "type": "function",
            "function": {
                "name": chat_name,
                "description": function.get("description"),
                "parameters": function.get("parameters") or {},
            },
        }
        self.add_chat_tool(chat_name, ToolSpec(kind="function", name=name, namespace=namespace), chat_tool)

    def add_custom_tool(self, tool: dict[str, Any]) -> None:
        name = tool_name_from_value(tool)
        if not name:
            return
        chat_tool = {
            "type": "function",
            "function": {
                "name": name,
                "description": tool.get("description") or "Custom Codex tool.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        CUSTOM_TOOL_INPUT_FIELD: {
                            "type": "string",
                            "description": "Input to pass to the custom Codex tool.",
                        }
                    },
                    "required": [CUSTOM_TOOL_INPUT_FIELD],
                },
            },
        }
        self.add_chat_tool(name, ToolSpec(kind="custom", name=name), chat_tool)

    def add_tool_search_tool(self) -> None:
        chat_tool = {
            "type": "function",
            "function": {
                "name": TOOL_SEARCH_PROXY_NAME,
                "description": "Search and load Codex tools, plugins, connectors, and MCP namespaces for the current task.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query for tools or connectors to load.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of tool groups to return.",
                        },
                    },
                    "required": ["query"],
                },
            },
        }
        self.add_chat_tool(TOOL_SEARCH_PROXY_NAME, ToolSpec(kind="tool_search", name=TOOL_SEARCH_PROXY_NAME), chat_tool)

    def add_namespace_tool(self, namespace_tool: dict[str, Any]) -> None:
        namespace = namespace_tool.get("name")
        children = namespace_tool.get("tools") or namespace_tool.get("children")
        if not isinstance(namespace, str) or not namespace.strip() or not isinstance(children, list):
            return
        for child in children:
            if isinstance(child, dict) and child.get("type") == "function":
                self.add_function_tool(child, namespace=namespace)

    def add_response_tool(self, tool: Any) -> None:
        if isinstance(tool, str):
            self.add_custom_tool({"type": "custom", "name": tool})
            return
        if not isinstance(tool, dict):
            return
        tool_type = tool.get("type")
        if tool_type in _HOSTED_TOOL_TYPES:
            policy = get_settings().unsupported_tool_policy
            if policy in {"reject", "error"}:
                raise UnsupportedInputItemError(
                    f"Hosted Responses tool type '{tool_type}' is not supported by this bridge.",
                    item_type=tool_type,
                )
            if policy == "passthrough":
                if tool not in self.chat_tools:
                    self.chat_tools.append(tool)
                return
            _logger.debug("Ignoring unsupported hosted tool type: %s", tool_type)
            return
        if tool_type == "function":
            self.add_function_tool(tool)
        elif tool_type == "custom":
            self.add_custom_tool(tool)
        elif tool_type == "tool_search":
            self.add_tool_search_tool()
        elif tool_type == "namespace":
            self.add_namespace_tool(tool)
