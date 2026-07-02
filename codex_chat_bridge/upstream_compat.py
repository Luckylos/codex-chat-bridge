from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .reasoning_policy import (
    CanonicalReasoningEffort,
    ReasoningRequestState,
    ReasoningWireMode,
    _error_mentions,
    build_reasoning_fallback_state,
)

Body = dict[str, Any]
CompatPredicate = Callable[[Body, str], bool]
CompatRewrite = Callable[[Body], Body]


def _error_mentions_any(error_body: str, *needles: str) -> bool:
    return any(_error_mentions(error_body, needle) for needle in needles)


@dataclass(frozen=True, slots=True)
class GenericCompatRule:
    label: str
    matches: CompatPredicate
    rewrite: CompatRewrite


def _rewrite_fields(body: Body, **updates: Any) -> Body:
    rewritten = dict(body)
    for key, value in updates.items():
        if value is None:
            rewritten.pop(key, None)
        else:
            rewritten[key] = value
    return rewritten


def _next_state(
    state: ReasoningRequestState,
    *,
    body: Body,
    canonical_effort: CanonicalReasoningEffort | None = None,
    wire_mode: ReasoningWireMode | None = None,
) -> ReasoningRequestState:
    return ReasoningRequestState(
        body=body,
        canonical_effort=canonical_effort or state.canonical_effort,
        wire_mode=wire_mode or state.wire_mode,
    )


def _top_p_out_of_range(body: Body, error_body: str) -> bool:
    return body.get("top_p") is not None and _error_mentions(error_body, "top_p")


def _apply_top_p_clamp(body: Body) -> Body:
    return _rewrite_fields(body, top_p=0.999)


def _stream_options_rejected(body: Body, error_body: str) -> bool:
    return body.get("stream_options") is not None and _error_mentions(error_body, "stream_options")


def _apply_strip_stream_options(body: Body) -> Body:
    return _rewrite_fields(body, stream_options=None)


def _include_usage_rejected(body: Body, error_body: str) -> bool:
    opts = body.get("stream_options")
    return isinstance(opts, dict) and opts.get("include_usage") is True and _error_mentions(error_body, "include_usage")


def _apply_disable_include_usage(body: Body) -> Body:
    opts = dict(body.get("stream_options") or {})
    opts["include_usage"] = False
    return _rewrite_fields(body, stream_options=opts)


def _parallel_tool_calls_rejected(body: Body, error_body: str) -> bool:
    return body.get("parallel_tool_calls") is not None and _error_mentions(error_body, "parallel_tool_calls")


def _apply_strip_parallel_tool_calls(body: Body) -> Body:
    return _rewrite_fields(body, parallel_tool_calls=None)


def _has_explicit_tool_choice_object(body: Body) -> bool:
    return isinstance(body.get("tool_choice"), dict)


_GENERIC_COMPAT_RULES: tuple[GenericCompatRule, ...] = (
    GenericCompatRule("top_p_out_of_range", _top_p_out_of_range, _apply_top_p_clamp),
    GenericCompatRule("include_usage_rejected", _include_usage_rejected, _apply_disable_include_usage),
    GenericCompatRule("stream_options_rejected", _stream_options_rejected, _apply_strip_stream_options),
    GenericCompatRule("parallel_tool_calls_rejected", _parallel_tool_calls_rejected, _apply_strip_parallel_tool_calls),
)


class UpstreamCompatPolicy:
    def generic_retry_state(
        self,
        state: ReasoningRequestState,
        error_body: str,
    ) -> tuple[str, ReasoningRequestState] | None:
        for rule in _GENERIC_COMPAT_RULES:
            if rule.matches(state.body, error_body):
                return rule.label, _next_state(state, body=rule.rewrite(state.body))
        return None

    def explicit_tool_choice_thinking_mode_retry_state(
        self,
        state: ReasoningRequestState,
        error_body: str,
        *,
        status_code: int = 400,
    ) -> tuple[str, ReasoningRequestState] | None:
        """Retry explicit tool_choice requests with reasoning disabled.

        Some upstreams (verified on deepseek-v4-flash via NewAPI) reject
        ``tool_choice`` object/required forms while default thinking mode is
        active, but accept the same payload once reasoning is explicitly
        disabled. Preserve caller intent when they already set reasoning by
        only applying this retry to requests whose reasoning mode was
        originally unspecified.
        """
        if state.canonical_effort != "unspecified":
            return None
        if not _has_explicit_tool_choice_object(state.body):
            return None
        if status_code == 400:
            if not _error_mentions(error_body, "tool_choice"):
                return None
            if not _error_mentions(error_body, "thinking mode"):
                return None
        else:
            if status_code not in (500, 503):
                return None
            if not state.body.get("stream"):
                return None
            if not _error_mentions_any(error_body, "empty_stream", "upstream stream closed before first payload"):
                return None
        return "explicit_tool_choice_disable_reasoning", _next_state(
            state,
            body=_rewrite_fields(state.body, thinking=None, reasoning_effort="none"),
            canonical_effort="none",
            wire_mode="effort_only",
        )

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
        return "unsupported_thinking_strip_raw_thinking", _next_state(
            state,
            body=_rewrite_fields(state.body, thinking=None),
        )

    def retry_state(
        self,
        state: ReasoningRequestState,
        error_body: str,
        *,
        status_code: int = 400,
    ) -> tuple[str, ReasoningRequestState] | None:
        if status_code == 400:
            generic_retry = self.generic_retry_state(state, error_body)
            if generic_retry is not None:
                return generic_retry

        explicit_tool_choice_retry = self.explicit_tool_choice_thinking_mode_retry_state(
            state,
            error_body,
            status_code=status_code,
        )
        if explicit_tool_choice_retry is not None:
            return explicit_tool_choice_retry

        if status_code != 400:
            return None

        reasoning_retry = build_reasoning_fallback_state(state, error_body)
        if reasoning_retry is not None:
            return reasoning_retry

        return self.raw_thinking_strip_retry_state(state, error_body)
