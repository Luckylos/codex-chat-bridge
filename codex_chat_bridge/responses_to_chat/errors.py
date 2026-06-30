from __future__ import annotations

from typing import Any

from ..errors import UnsupportedInputItemError


class UnsupportedResponsesInputItemError(UnsupportedInputItemError):
    def __init__(self, item_type: str | None, item: Any, detail: str | None = None) -> None:
        label = item_type or type(item).__name__
        msg = f"Unsupported top-level Responses input item: {label}"
        if detail:
            msg = f"{msg}: {detail}"
        super().__init__(msg, item_type=item_type, detail=detail)
        self.item = item
