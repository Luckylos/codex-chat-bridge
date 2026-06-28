from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Literal

CanonicalReasoningEffort = Literal["unspecified", "none", "high", "xhigh"]
ReasoningProviderBucket = Literal["openai_like", "deepseek", "glm", "kimi"]
ReasoningWireMode = Literal[
    "provider_default",
    "effort_only",
    "glm_disabled",
    "glm_enabled_with_effort",
    "glm_enabled_only",
]


@dataclass(frozen=True, slots=True)
class ReasoningRequestState:
    body: dict[str, Any]
    bucket: ReasoningProviderBucket
    canonical_effort: CanonicalReasoningEffort
    wire_mode: ReasoningWireMode


@dataclass(frozen=True, slots=True)
class ReasoningFallbackStep:
    label: str
    wire_mode: ReasoningWireMode


_BUCKET_RULES: tuple[tuple[re.Pattern[str], ReasoningProviderBucket], ...] = (
    (re.compile(r"(?:^|[/\-])(deepseek)", re.IGNORECASE), "deepseek"),
    (re.compile(r"(?:^|[/\-])(glm|zhipu|bigmodel)", re.IGNORECASE), "glm"),
    (re.compile(r"(?:^|[/\-])(kimi|moonshot)", re.IGNORECASE), "kimi"),
)

_DEFAULT_BUCKET: ReasoningProviderBucket = "openai_like"


def normalize_canonical_reasoning_effort(value: Any) -> CanonicalReasoningEffort:
    if not isinstance(value, str):
        return "unspecified"

    normalized = value.strip().lower()
    if not normalized:
        return "unspecified"
    if normalized in {"off", "disabled", "false", "none", "minimal"}:
        return "none"
    if normalized in {"low", "medium", "high"}:
        return "high"
    if normalized in {"xhigh", "max"}:
        return "xhigh"
    return "high"


def select_reasoning_provider_bucket(model: str | None) -> ReasoningProviderBucket:
    normalized_model = (model or "").strip()
    for pattern, bucket in _BUCKET_RULES:
        if pattern.search(normalized_model):
            return bucket
    return _DEFAULT_BUCKET


def _strip_reasoning_fields(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k not in {"thinking", "reasoning_effort"}}


def infer_canonical_reasoning_effort(body: dict[str, Any]) -> CanonicalReasoningEffort:
    explicit_effort = normalize_canonical_reasoning_effort(body.get("reasoning_effort"))
    if explicit_effort != "unspecified":
        return explicit_effort

    thinking = body.get("thinking")
    if isinstance(thinking, dict) and thinking.get("type") == "disabled":
        return "none"

    return "unspecified"


def _select_initial_wire_mode(
    bucket: ReasoningProviderBucket,
    canonical_effort: CanonicalReasoningEffort,
) -> ReasoningWireMode:
    if canonical_effort == "unspecified":
        return "provider_default"

    if bucket in {"openai_like", "deepseek"}:
        return "effort_only"

    if bucket == "glm":
        if canonical_effort == "none":
            return "glm_disabled"
        return "glm_enabled_with_effort"

    return "provider_default"


def _encode_for_mode(
    body: dict[str, Any],
    *,
    bucket: ReasoningProviderBucket,
    canonical_effort: CanonicalReasoningEffort,
    wire_mode: ReasoningWireMode,
) -> dict[str, Any]:
    encoded = _strip_reasoning_fields(body)

    if wire_mode == "provider_default":
        return encoded

    if wire_mode == "effort_only":
        if canonical_effort == "unspecified":
            return encoded
        encoded["reasoning_effort"] = canonical_effort
        return encoded

    if bucket != "glm":
        raise ValueError(f"wire_mode={wire_mode!r} is only valid for glm bucket")

    if wire_mode == "glm_disabled":
        encoded["thinking"] = {"type": "disabled"}
        return encoded

    if wire_mode == "glm_enabled_only":
        encoded["thinking"] = {"type": "enabled"}
        return encoded

    if wire_mode == "glm_enabled_with_effort":
        encoded["thinking"] = {"type": "enabled"}
        if canonical_effort != "unspecified":
            encoded["reasoning_effort"] = canonical_effort
        return encoded

    raise ValueError(f"Unknown reasoning wire mode: {wire_mode}")


def build_initial_reasoning_state(body: dict[str, Any]) -> ReasoningRequestState:
    body_copy = dict(body)
    bucket = select_reasoning_provider_bucket(str(body_copy.get("model") or ""))
    canonical_effort = infer_canonical_reasoning_effort(body_copy)
    wire_mode = _select_initial_wire_mode(bucket, canonical_effort)
    encoded_body = _encode_for_mode(
        body_copy,
        bucket=bucket,
        canonical_effort=canonical_effort,
        wire_mode=wire_mode,
    )
    return ReasoningRequestState(
        body=encoded_body,
        bucket=bucket,
        canonical_effort=canonical_effort,
        wire_mode=wire_mode,
    )


def _error_mentions(error_text: str, needle: str) -> bool:
    return needle in error_text.lower()


def build_reasoning_fallback_step(
    state: ReasoningRequestState,
    error_text: str,
) -> ReasoningFallbackStep | None:
    thinking_rejected = _error_mentions(error_text, "thinking")
    effort_rejected = _error_mentions(error_text, "reasoning_effort")

    if not thinking_rejected and not effort_rejected:
        return None

    if state.wire_mode == "provider_default":
        return None

    if state.bucket in {"openai_like", "deepseek"}:
        if effort_rejected:
            return ReasoningFallbackStep(
                label="unsupported_reasoning_effort_to_provider_default",
                wire_mode="provider_default",
            )
        return None

    if state.bucket == "glm":
        if state.wire_mode == "glm_enabled_with_effort":
            if thinking_rejected and effort_rejected:
                return ReasoningFallbackStep(
                    label="unsupported_glm_reasoning_fields_to_provider_default",
                    wire_mode="provider_default",
                )
            if effort_rejected:
                return ReasoningFallbackStep(
                    label="unsupported_reasoning_effort_to_glm_enabled_only",
                    wire_mode="glm_enabled_only",
                )
            if thinking_rejected:
                return ReasoningFallbackStep(
                    label="unsupported_thinking_to_provider_default",
                    wire_mode="provider_default",
                )

        if state.wire_mode in {"glm_enabled_only", "glm_disabled"} and thinking_rejected:
            return ReasoningFallbackStep(
                label="unsupported_thinking_to_provider_default",
                wire_mode="provider_default",
            )

    return None


def build_reasoning_fallback_state(
    state: ReasoningRequestState,
    error_text: str,
) -> tuple[str, ReasoningRequestState] | None:
    fallback_step = build_reasoning_fallback_step(state, error_text)
    if fallback_step is None:
        return None

    encoded_body = _encode_for_mode(
        state.body,
        bucket=state.bucket,
        canonical_effort=state.canonical_effort,
        wire_mode=fallback_step.wire_mode,
    )
    return fallback_step.label, ReasoningRequestState(
        body=encoded_body,
        bucket=state.bucket,
        canonical_effort=state.canonical_effort,
        wire_mode=fallback_step.wire_mode,
    )
