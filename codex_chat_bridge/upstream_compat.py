from __future__ import annotations

from typing import Any

from .reasoning_policy import ReasoningRequestState, build_reasoning_fallback_state


def _error_mentions(error_body: str, needle: str) -> bool:
    return needle in error_body.lower()


def _rewrite_fields(body: dict[str, Any], **updates: Any) -> dict[str, Any]:
    rewritten = dict(body)
    for key, value in updates.items():
        if value is None:
            rewritten.pop(key, None)
        else:
            rewritten[key] = value
    return rewritten


def _top_p_out_of_range(body: dict[str, Any], error_body: str) -> bool:
    return body.get("top_p") is not None and _error_mentions(error_body, "top_p")


def _apply_top_p_clamp(body: dict[str, Any]) -> dict[str, Any]:
    return _rewrite_fields(body, top_p=0.999)


def _stream_options_rejected(body: dict[str, Any], error_body: str) -> bool:
    return body.get("stream_options") is not None and _error_mentions(error_body, "stream_options")


def _apply_strip_stream_options(body: dict[str, Any]) -> dict[str, Any]:
    return _rewrite_fields(body, stream_options=None)


def _include_usage_rejected(body: dict[str, Any], error_body: str) -> bool:
    opts = body.get("stream_options")
    return isinstance(opts, dict) and opts.get("include_usage") is True and _error_mentions(error_body, "include_usage")


def _apply_disable_include_usage(body: dict[str, Any]) -> dict[str, Any]:
    return _rewrite_fields(body, stream_options={"include_usage": False})


def _parallel_tool_calls_rejected(body: dict[str, Any], error_body: str) -> bool:
    return body.get("parallel_tool_calls") is not None and _error_mentions(error_body, "parallel_tool_calls")


def _apply_strip_parallel_tool_calls(body: dict[str, Any]) -> dict[str, Any]:
    return _rewrite_fields(body, parallel_tool_calls=None)


_GENERIC_COMPAT_RULES: list[tuple[str, Any, Any]] = [
    ("top_p_out_of_range", _top_p_out_of_range, _apply_top_p_clamp),
    ("include_usage_rejected", _include_usage_rejected, _apply_disable_include_usage),
    ("stream_options_rejected", _stream_options_rejected, _apply_strip_stream_options),
    ("parallel_tool_calls_rejected", _parallel_tool_calls_rejected, _apply_strip_parallel_tool_calls),
]


class UpstreamCompatPolicy:
    def generic_retry_state(
        self,
        state: ReasoningRequestState,
        error_body: str,
    ) -> tuple[str, ReasoningRequestState] | None:
        for label, check, apply in _GENERIC_COMPAT_RULES:
            if check(state.body, error_body):
                retried_body = apply(state.body)
                return label, ReasoningRequestState(
                    body=retried_body,
                    bucket=state.bucket,
                    canonical_effort=state.canonical_effort,
                    wire_mode=state.wire_mode,
                )
        return None

    def raw_thinking_strip_retry_state(
        self,
        state: ReasoningRequestState,
        error_body: str,
    ) -> tuple[str, ReasoningRequestState] | None:
        if state.wire_mode != "provider_default":
            return None
        if state.body.get("thinking") is None:
            return None
        if not _error_mentions(error_body, "thinking"):
            return None
        retried_body = _rewrite_fields(state.body, thinking=None)
        return "unsupported_thinking_strip_raw_thinking", ReasoningRequestState(
            body=retried_body,
            bucket=state.bucket,
            canonical_effort=state.canonical_effort,
            wire_mode="provider_default",
        )

    def retry_state(
        self,
        state: ReasoningRequestState,
        error_body: str,
    ) -> tuple[str, ReasoningRequestState] | None:
        generic_retry = self.generic_retry_state(state, error_body)
        if generic_retry is not None:
            return generic_retry

        reasoning_retry = build_reasoning_fallback_state(state, error_body)
        if reasoning_retry is not None:
            return reasoning_retry

        return self.raw_thinking_strip_retry_state(state, error_body)
