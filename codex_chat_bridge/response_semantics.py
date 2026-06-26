from __future__ import annotations

import json
from typing import Any


def canonicalize_tool_arguments(arguments: object) -> str:
    if arguments is None:
        return "{}"
    if isinstance(arguments, str):
        raw = arguments.strip()
        if not raw:
            return "{}"
        try:
            parsed = json.loads(raw)
        except Exception:
            return arguments
        return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return json.dumps(arguments, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def map_chat_usage(usage: dict | None) -> dict:
    if not usage:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0))
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens", 0))
    total_tokens = usage.get("total_tokens", input_tokens + output_tokens)
    result = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
    if "completion_tokens_details" in usage:
        result["output_tokens_details"] = usage["completion_tokens_details"]
    if "prompt_tokens_details" in usage:
        result["input_tokens_details"] = usage["prompt_tokens_details"]
    return result


def response_status_from_finish_reason(finish_reason: str | None) -> str:
    return "incomplete" if finish_reason == "length" else "completed"
