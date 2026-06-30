from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Literal

CanonicalReasoningEffort = Literal["unspecified", "none", "high", "xhigh"]
ReasoningWireMode = Literal["provider_default", "effort_only"]

# Internal discriminator: effort-passing buckets use effort_only; kimi uses
# provider_default (no reasoning parameters).  The bucket is derived from
# model name and never leaves this module.
_Bucket = Literal["effort", "passthrough"]


@dataclass(frozen=True, slots=True)
class ReasoningRequestState:
    body: dict[str, Any]
    canonical_effort: CanonicalReasoningEffort
    wire_mode: ReasoningWireMode


@dataclass(frozen=True, slots=True)
class ReasoningFallbackStep:
    label: str
    wire_mode: ReasoningWireMode


# Model-name patterns → internal bucket.
# "effort"  = accept reasoning_effort (openai_like, deepseek, glm)
# "passthrough" = preserve provider defaults, send no reasoning params (kimi)
_BUCKET_RULES: tuple[tuple[re.Pattern[str], _Bucket], ...] = (
    (re.compile(r"(?:^|[/\-])(kimi|moonshot)", re.IGNORECASE), "passthrough"),
    # The remaining effort-passing families — order does not matter since
    # they all map to the same bucket.
    (re.compile(r"(?:^|[/\-])(deepseek)", re.IGNORECASE), "effort"),
    (re.compile(r"(?:^|[/\-])(glm|zhipu|bigmodel)", re.IGNORECASE), "effort"),
)

_DEFAULT_BUCKET: _Bucket = "effort"


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


def _select_bucket(model: str | None) -> _Bucket:
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


def _select_wire_mode(
    bucket: _Bucket,
    canonical_effort: CanonicalReasoningEffort,
) -> ReasoningWireMode:
    if canonical_effort == "unspecified":
        return "provider_default"

    if bucket == "effort":
        return "effort_only"

    # passthrough: preserve provider defaults regardless of explicit effort
    return "provider_default"


def _encode_for_mode(
    body: dict[str, Any],
    *,
    canonical_effort: CanonicalReasoningEffort,
    wire_mode: ReasoningWireMode,
) -> dict[str, Any]:
    encoded = _strip_reasoning_fields(body)

    if wire_mode == "provider_default":
        return encoded

    if wire_mode == "effort_only":
        if canonical_effort != "unspecified":
            encoded["reasoning_effort"] = canonical_effort
        return encoded

    raise ValueError(f"Unknown reasoning wire mode: {wire_mode}")


# Public API wrappers preserving existing call signatures.

def select_reasoning_provider_bucket(model: str | None) -> str:
    """Public: returns the legacy bucket name for backward compatibility.

    Mapping: kimi → "kimi", deepseek → "deepseek", glm → "glm",
    everything else → "openai_like".
    """
    normalized_model = (model or "").strip()
    for pattern, _ in _BUCKET_RULES:
        m = pattern.search(normalized_model)
        if m:
            matched = m.group(1).lower()
            if matched in {"kimi", "moonshot"}:
                return "kimi"
            if matched in {"deepseek"}:
                return "deepseek"
            if matched in {"glm", "zhipu", "bigmodel"}:
                return "glm"
    return "openai_like"


def build_initial_reasoning_state(body: dict[str, Any]) -> ReasoningRequestState:
    body_copy = dict(body)
    bucket = _select_bucket(str(body_copy.get("model") or ""))
    canonical_effort = infer_canonical_reasoning_effort(body_copy)
    wire_mode = _select_wire_mode(bucket, canonical_effort)
    encoded_body = _encode_for_mode(
        body_copy,
        canonical_effort=canonical_effort,
        wire_mode=wire_mode,
    )
    return ReasoningRequestState(
        body=encoded_body,
        canonical_effort=canonical_effort,
        wire_mode=wire_mode,
    )


def _error_mentions(error_text: str, needle: str) -> bool:
    return needle in error_text.lower()


def build_reasoning_fallback_step(
    state: ReasoningRequestState,
    error_text: str,
) -> ReasoningFallbackStep | None:
    if not _error_mentions(error_text, "reasoning_effort"):
        return None

    if state.wire_mode == "provider_default":
        return None

    # effort_rejected is guaranteed True here (checked above)
    return ReasoningFallbackStep(
        label="unsupported_reasoning_effort_to_provider_default",
        wire_mode="provider_default",
    )


def build_reasoning_fallback_state(
    state: ReasoningRequestState,
    error_text: str,
) -> tuple[str, ReasoningRequestState] | None:
    fallback_step = build_reasoning_fallback_step(state, error_text)
    if fallback_step is None:
        return None

    encoded_body = _encode_for_mode(
        state.body,
        canonical_effort=state.canonical_effort,
        wire_mode=fallback_step.wire_mode,
    )
    return fallback_step.label, ReasoningRequestState(
        body=encoded_body,
        canonical_effort=state.canonical_effort,
        wire_mode=fallback_step.wire_mode,
    )
