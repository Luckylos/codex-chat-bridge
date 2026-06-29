from __future__ import annotations

import unittest

from codex_chat_bridge.reasoning_policy import (
    build_initial_reasoning_state,
    build_reasoning_fallback_state,
    infer_canonical_reasoning_effort,
    normalize_canonical_reasoning_effort,
    select_reasoning_provider_bucket,
)


class NormalizeEffortTests(unittest.TestCase):
    def test_none_and_empty(self) -> None:
        self.assertEqual(normalize_canonical_reasoning_effort(None), "unspecified")
        self.assertEqual(normalize_canonical_reasoning_effort(""), "unspecified")

    def test_disabled_variants(self) -> None:
        self.assertEqual(normalize_canonical_reasoning_effort("none"), "none")
        self.assertEqual(normalize_canonical_reasoning_effort("disabled"), "none")
        self.assertEqual(normalize_canonical_reasoning_effort("off"), "none")
        self.assertEqual(normalize_canonical_reasoning_effort("false"), "none")

    def test_effort_levels(self) -> None:
        self.assertEqual(normalize_canonical_reasoning_effort("low"), "high")
        self.assertEqual(normalize_canonical_reasoning_effort("medium"), "high")
        self.assertEqual(normalize_canonical_reasoning_effort("high"), "high")
        self.assertEqual(normalize_canonical_reasoning_effort("max"), "xhigh")
        self.assertEqual(normalize_canonical_reasoning_effort("xhigh"), "xhigh")


class BucketSelectionTests(unittest.TestCase):
    """Legacy bucket names are preserved for backward compatibility."""

    def test_by_model_prefix(self) -> None:
        self.assertEqual(select_reasoning_provider_bucket("deepseek-v4-flash"), "deepseek")
        self.assertEqual(select_reasoning_provider_bucket("glm-5.2"), "glm")
        self.assertEqual(select_reasoning_provider_bucket("kimi-k2"), "kimi")
        self.assertEqual(select_reasoning_provider_bucket("gpt-5"), "openai_like")

    def test_with_relay_prefix(self) -> None:
        # NewAPI channel prefix formats: "channel-model" or "provider/model"
        self.assertEqual(select_reasoning_provider_bucket("z-ai/glm-5.1"), "glm")
        self.assertEqual(select_reasoning_provider_bucket("evomap-glm-5.1"), "glm")
        self.assertEqual(select_reasoning_provider_bucket("deepseek-ai/deepseek-v4-flash"), "deepseek")
        self.assertEqual(select_reasoning_provider_bucket("moonshotai/kimi-k2.6"), "kimi")
        self.assertEqual(select_reasoning_provider_bucket("evomap-kimi-k2.6"), "kimi")
        # Relay-prefixed openai_like models stay openai_like
        self.assertEqual(select_reasoning_provider_bucket("openai/gpt-oss-120b"), "openai_like")
        self.assertEqual(select_reasoning_provider_bucket("evomap-claude-opus"), "openai_like")


class InferEffortTests(unittest.TestCase):
    def test_prefers_reasoning_effort_field(self) -> None:
        self.assertEqual(infer_canonical_reasoning_effort({"reasoning_effort": "medium"}), "high")
        self.assertEqual(infer_canonical_reasoning_effort({"reasoning_effort": "max"}), "xhigh")
        self.assertEqual(infer_canonical_reasoning_effort({"thinking": {"type": "disabled"}}), "none")
        self.assertEqual(infer_canonical_reasoning_effort({}), "unspecified")


class InitialWireModeTests(unittest.TestCase):
    """Three buckets (openai_like, deepseek, glm) behave identically:
    effort_only when effort is explicit, provider_default otherwise.
    Kimi always uses provider_default."""

    def test_openai_like_explicit_high_uses_effort_only(self) -> None:
        state = build_initial_reasoning_state({
            "model": "gpt-5",
            "reasoning_effort": "high",
        })
        self.assertEqual(select_reasoning_provider_bucket("gpt-5"), "openai_like")
        self.assertEqual(state.canonical_effort, "high")
        self.assertEqual(state.wire_mode, "effort_only")
        self.assertEqual(state.body["reasoning_effort"], "high")
        self.assertNotIn("thinking", state.body)

    def test_deepseek_explicit_high_uses_effort_only(self) -> None:
        state = build_initial_reasoning_state({
            "model": "deepseek-v4-flash",
            "reasoning_effort": "high",
        })
        self.assertEqual(select_reasoning_provider_bucket("deepseek-v4-flash"), "deepseek")
        self.assertEqual(state.wire_mode, "effort_only")
        self.assertEqual(state.body["reasoning_effort"], "high")
        self.assertNotIn("thinking", state.body)

    def test_deepseek_unspecified_preserves_provider_default(self) -> None:
        state = build_initial_reasoning_state({
            "model": "deepseek-v4-flash",
        })
        self.assertEqual(state.wire_mode, "provider_default")
        self.assertNotIn("thinking", state.body)
        self.assertNotIn("reasoning_effort", state.body)

    def test_glm_explicit_high_uses_effort_only(self) -> None:
        # GLM via NVIDIA NIM accepts reasoning_effort but rejects thinking.
        state = build_initial_reasoning_state({
            "model": "glm-5.2",
            "reasoning_effort": "high",
        })
        self.assertEqual(select_reasoning_provider_bucket("glm-5.2"), "glm")
        self.assertEqual(state.wire_mode, "effort_only")
        self.assertEqual(state.body["reasoning_effort"], "high")
        self.assertNotIn("thinking", state.body)

    def test_glm_explicit_none_uses_effort_only(self) -> None:
        state = build_initial_reasoning_state({
            "model": "glm-5.2",
            "reasoning_effort": "none",
        })
        self.assertEqual(state.wire_mode, "effort_only")
        self.assertEqual(state.body["reasoning_effort"], "none")
        self.assertNotIn("thinking", state.body)

    def test_glm_unspecified_preserves_provider_default(self) -> None:
        state = build_initial_reasoning_state({
            "model": "glm-5.2",
        })
        self.assertEqual(state.wire_mode, "provider_default")
        self.assertNotIn("thinking", state.body)
        self.assertNotIn("reasoning_effort", state.body)

    def test_kimi_ignores_explicit_effort_and_preserves_default(self) -> None:
        state = build_initial_reasoning_state({
            "model": "kimi-k2.6",
            "reasoning_effort": "xhigh",
        })
        self.assertEqual(select_reasoning_provider_bucket("kimi-k2.6"), "kimi")
        self.assertEqual(state.wire_mode, "provider_default")
        self.assertNotIn("thinking", state.body)
        self.assertNotIn("reasoning_effort", state.body)

    def test_no_model_defaults_to_effort_bucket(self) -> None:
        state = build_initial_reasoning_state({
            "reasoning_effort": "high",
        })
        self.assertEqual(state.wire_mode, "effort_only")
        self.assertEqual(state.body["reasoning_effort"], "high")


class FallbackTests(unittest.TestCase):
    """All effort_only states share the same fallback: reasoning_effort
    rejection → provider_default.  provider_default has no fallback."""

    def test_openai_effort_rejection_falls_back_to_provider_default(self) -> None:
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

    def test_glm_effort_rejection_falls_back_to_provider_default(self) -> None:
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

    def test_deepseek_effort_rejection_falls_back_to_provider_default(self) -> None:
        initial_state = build_initial_reasoning_state({
            "model": "deepseek-v4-flash",
            "reasoning_effort": "high",
        })
        retry = build_reasoning_fallback_state(initial_state, "Unsupported parameter(s): reasoning_effort")
        assert retry is not None
        label, retry_state = retry
        self.assertEqual(label, "unsupported_reasoning_effort_to_provider_default")
        self.assertEqual(retry_state.wire_mode, "provider_default")
        self.assertNotIn("thinking", retry_state.body)
        self.assertNotIn("reasoning_effort", retry_state.body)

    def test_provider_default_has_no_fallback(self) -> None:
        initial_state = build_initial_reasoning_state({
            "model": "kimi-k2.6",
            "reasoning_effort": "high",
        })
        retry = build_reasoning_fallback_state(initial_state, "Unsupported parameter(s): reasoning_effort")
        self.assertIsNone(retry)

    def test_non_reasoning_400_returns_none(self) -> None:
        initial_state = build_initial_reasoning_state({
            "model": "gpt-5",
            "reasoning_effort": "high",
        })
        retry = build_reasoning_fallback_state(initial_state, "Internal server error")
        self.assertIsNone(retry)
