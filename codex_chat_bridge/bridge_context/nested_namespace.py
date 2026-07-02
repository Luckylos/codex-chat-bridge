from __future__ import annotations

import json
from dataclasses import dataclass

from .models import ToolSpec


@dataclass(frozen=True, slots=True)
class NestedNamespaceResolution:
    action_name: str | None
    normalized_arguments: str


def resolve_nested_namespace_arguments(
    spec: ToolSpec,
    arguments_json: str,
) -> NestedNamespaceResolution:
    """Normalize nested namespace arguments and extract a validated action name.

    The namespace tool schemas encode the concrete action inside an ``action``
    field. For ``nested_anyof`` schemas, the real function arguments live under
    ``params`` and need to be flattened back into the final arguments object.

    When the payload is incomplete, malformed, or the action is unknown, the
    original argument string is preserved so callers can decide whether to keep
    buffering, fall back to the namespace name, or surface the raw payload.
    """
    if not arguments_json:
        return NestedNamespaceResolution(action_name=None, normalized_arguments=arguments_json)

    try:
        args_obj = json.loads(arguments_json)
    except (json.JSONDecodeError, ValueError):
        return NestedNamespaceResolution(action_name=None, normalized_arguments=arguments_json)

    if not isinstance(args_obj, dict):
        return NestedNamespaceResolution(action_name=None, normalized_arguments=arguments_json)

    raw_action = args_obj.pop("action", None)
    if spec.namespace_strategy == "nested_anyof":
        params_val = args_obj.pop("params", None)
        if isinstance(params_val, dict):
            args_obj.update(params_val)

    normalized_arguments = json.dumps(
        args_obj,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    action_name = raw_action if isinstance(raw_action, str) and raw_action in (spec.actions or []) else None
    return NestedNamespaceResolution(action_name=action_name, normalized_arguments=normalized_arguments)
