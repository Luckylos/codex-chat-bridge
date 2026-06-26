from __future__ import annotations

from typing import Any


class UnsupportedResponsesInputItemError(ValueError):
    def __init__(self, item_type: str | None, item: Any) -> None:
        label = item_type or type(item).__name__
        super().__init__(f"Unsupported top-level Responses input item: {label}")
        self.item_type = item_type
        self.item = item
