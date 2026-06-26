from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ToolSpec:
    kind: str
    name: str
    namespace: str | None = None
