from __future__ import annotations

import unittest

from codex_chat_bridge.reasoning_policy import (
    build_initial_reasoning_state,
    build_reasoning_fallback_state,
    infer_canonical_reasoning_effort,
    normalize_canonical_reasoning_effort,
    select_reasoning_provider_bucket,
)


class ReasoningPolicyTests(unittest.TestCase):
    def test_normalize_canonical_reasoning_effort(self) -> None:
        self.assertEqual(normalize_canonical_reasoning_effort(None), "unspecified")
        self.assertEqual(normalize_canonical_reasoning_effort("none"), "none")
        self.assertEqual(normalize_canonical_reasoning_effort("disabled"), "none")
        self.assertEqual(normalize_canonical_reasoning_effort("medium"), "high")
        self.assertEqual(normalize_canonical_reasoning_effort("high"), "high")
        self.assertEqual(normalize_canonical_reasoning_effort("max"), "xhigh")
        self.assertEqual(normalize_canonical_reasoning_effort("xhigh"), "xhigh")

    def test_select_reasoning_provider_bucket_by_model_prefix(self) -> None:
        self.assertEqual(select_reasoning_provider_bucket("deepseek-v4-flash"), "deepseek")
        self.assertEqual(select_reasoning_provider_bucket("glm-5.2"), "glm")
        self.assertEqual(select_reasoning_provider_bucket("kimi-k2"), "kimi")
        self.assertEqual(select_reasoning_provider_bucket("gpt-5"), "openai_like")

    def test_select_reasoning_provider_bucket_with_relay_prefix(self) -> None:
        # NewAPI channel prefix formats: "channel-model" or "provider/model"
        self.assertEqual(select_reasoning_provider_bucket("z-ai/glm-5.1"), "glm")
        self.assertEqual(select_reasoning_provider_bucket("evomap-glm-5.1"), "glm")
        self.assertEqual(select_reasoning_provider_bucket("deepseek-ai/deepseek-v4-flash"), "deepseek")
        self.assertEqual(select_reasoning_provider_bucket("moonshotai/kimi-k2.6"), "kimi")
        self.assertEqual(select_reasoning_provider_bucket("evomap-kimi-k2.6"), "kimi")
        # Relay-prefixed openai_like models stay openai_like
        self.assertEqual(select_reasoning_provider_bucket("openai/gpt-oss-120b"), "openai_like")
        self.assertEqual(select_reasoning_provider_bucket("evomap-claude-opus"), "openai_like")

    def test_infer_canonical_reasoning_effort_prefers_reasoning_effort_field(self) -> None:
        self.assertEqual(infer_canonical_reasoning_effort({"reasoning_effort": "medium"}), "high")
        self.assertEqual(infer_canonical_reasoning_effort({"reasoning_effort": "max"}), "xhigh")
        self.assertEqual(infer_canonical_reasoning_effort({"thinking": {"type": "disabled"}}), "none")
        self.assertEqual(infer_canonical_reasoning_effort({}), "unspecified")

    def test_build_initial_reasoning_state_openai_like_explicit_high_uses_effort_only(self) -> None:
        state = build_initial_reasoning_state({
            "model": "gpt-5",
            "reasoning_effort": "high",
        })
        self.assertEqual(state.bucket, "openai_like")
        self.assertEqual(state.canonical_effort, "high")
        self.assertEqual(state.wire_mode, "effort_only")
        self.assertEqual(state.body["reasoning_effort"], "high")
        self.assertNotIn("thinking", state.body)

    def test_build_initial_reasoning_state_deepseek_unspecified_preserves_provider_default(self) -> None:
        state = build_initial_reasoning_state({
            "model": "deepseek-v4-flash",
        })
        self.assertEqual(state.bucket, "deepseek")
        self.assertEqual(state.canonical_effort, "unspecified")
        self.assertEqual(state.wire_mode, "provider_default")
        self.assertNotIn("thinking", state.body)
        self.assertNotIn("reasoning_effort", state.body)

    def test_build_initial_reasoning_state_glm_high_uses_effort_only(self) -> None:
        # GLM via NVIDIA NIM / standard OpenAI-compatible gateways accepts
        # reasoning_effort but rejects thinking — same wire_mode as openai_like.
        state = build_initial_reasoning_state({
            "model": "glm-5.2",
            "reasoning_effort": "high",
        })
        self.assertEqual(state.bucket, "glm")
        self.assertEqual(state.canonical_effort, "high")
        self.assertEqual(state.wire_mode, "effort_only")
        self.assertEqual(state.body["reasoning_effort"], "high")
        self.assertNotIn("thinking", state.body)

    def test_build_initial_reasoning_state_glm_none_uses_effort_only(self) -> None:
        state = build_initial_reasoning_state({
            "model": "glm-5.2",
            "reasoning_effort": "none",
        })
        self.assertEqual(state.wire_mode, "effort_only")
        self.assertEqual(state.body["reasoning_effort"], "none")
        self.assertNotIn("thinking", state.body)

    def test_build_initial_reasoning_state_kimi_ignores_explicit_effort_and_preserves_default(self) -> None:
        state = build_initial_reasoning_state({
            "model": "kimi-k2.6",
            "reasoning_effort": "xhigh",
        })
        self.assertEqual(state.bucket, "kimi")
        self.assertEqual(state.canonical_effort, "xhigh")
        self.assertEqual(state.wire_mode, "provider_default")
        self.assertNotIn("thinking", state.body)
        self.assertNotIn("reasoning_effort", state.body)

    def test_glm_effort_only_rejection_falls_back_to_provider_default(self) -> None:
        # GLM now uses effort_only just like openai_like/deepseek.
        # If the upstream rejects reasoning_effort, fall back to provider_default.
        initial_state = build_initial_reasoning_state({
            "model": "glm-5.2",
            "reasoning_effort": "high",
        })
        retry = build_reasoning_fallback_state(initial_state, "Unsupported parameter(s): reasoning_effort")
        assert retry is not None
        label, retry_state = retry
        self.assertEqual(label, "unsupported_reasoning_effort_to_provider_default")
        self.assertEqual(retry_state.wire_mode, "provider_default")
        self.assertNotIn("thinking", retry_state.body)
        self.assertNotIn("reasoning_effort", retry_state.body)

    def test_glm_thinking_rejection_falls_back_to_provider_default(self) -> None:
        # Construct a synthetic glm_enabled_with_effort state to verify the
        # fallback path still works even though the initial selection no
        # longer produces this wire_mode for GLM.
        from codex_chat_bridge.reasoning_policy import ReasoningRequestState, _encode_for_mode
        body = _encode_for_mode(
            {"model": "glm-5.2", "reasoning_effort": "high"},
            bucket="glm",
            canonical_effort="high",
            wire_mode="glm_enabled_with_effort",
        )
        initial_state = ReasoningRequestState(
            body=body,
            bucket="glm",
            canonical_effort="high",
            wire_mode="glm_enabled_with_effort",
        )
        retry = build_reasoning_fallback_state(initial_state, "Unsupported parameter(s): thinking")
        assert retry is not None
        label, retry_state = retry
        self.assertEqual(label, "unsupported_thinking_to_provider_default")
        self.assertEqual(retry_state.wire_mode, "provider_default")
        self.assertNotIn("thinking", retry_state.body)
        self.assertNotIn("reasoning_effort", retry_state.body)

    def test_effort_only_rejection_falls_back_to_provider_default(self) -> None:
        initial_state = build_initial_reasoning_state({
            "model": "gpt-5",
            "reasoning_effort": "xhigh",
        })
        retry = build_reasoning_fallback_state(initial_state, "Unsupported parameter(s): reasoning_effort")
        assert retry is not None
        label, retry_state = retry
        self.assertEqual(label, "unsupported_reasoning_effort_to_provider_default")
        self.assertEqual(retry_state.wire_mode, "provider_default")
        self.assertNotIn("thinking", retry_state.body)
        self.assertNotIn("reasoning_effort", retry_state.body)
