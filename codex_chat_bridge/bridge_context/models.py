from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ToolSpec:
    kind: str  # "function" | "custom" | "tool_search" | "namespace"
    name: str
    namespace: str | None = None
    namespace_strategy: str | None = None  # "nested_oneof" | "nested_anyof" | "flat" | None
    actions: list[str] | None = None       # Sub-tool names when kind="namespace"

    @property
    def is_nested_namespace(self) -> bool:
        """True when this is a namespace tool with nested_oneof or nested_anyof strategy."""
        return (
            self.kind == "namespace"
            and self.namespace_strategy in ("nested_oneof", "nested_anyof")
        )
