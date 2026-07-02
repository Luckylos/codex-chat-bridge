from __future__ import annotations

import unittest

from codex_chat_bridge.reasoning_policy import build_initial_reasoning_state
from codex_chat_bridge.upstream_compat import UpstreamCompatPolicy


class GenericCompatRuleTests(unittest.TestCase):
    def test_top_p_is_clamped(self) -> None:
        state = build_initial_reasoning_state({"model": "gpt-5", "top_p": 1.5})
        retry = UpstreamCompatPolicy().retry_state(state, "Unsupported parameter(s): top_p")
        assert retry is not None
        label, next_state = retry
        self.assertEqual(label, "top_p_out_of_range")
        self.assertEqual(next_state.body["top_p"], 0.999)
        self.assertEqual(next_state.canonical_effort, state.canonical_effort)
        self.assertEqual(next_state.wire_mode, state.wire_mode)

    def test_include_usage_is_disabled_before_stream_options_are_stripped(self) -> None:
        state = build_initial_reasoning_state(
            {
                "model": "gpt-5",
                "stream_options": {"include_usage": True, "extra": "keep-me"},
            }
        )
        retry = UpstreamCompatPolicy().retry_state(state, "Unsupported parameter(s): include_usage")
        assert retry is not None
        label, next_state = retry
        self.assertEqual(label, "include_usage_rejected")
        self.assertEqual(next_state.body["stream_options"], {"include_usage": False, "extra": "keep-me"})

    def test_parallel_tool_calls_can_be_removed(self) -> None:
        state = build_initial_reasoning_state({"model": "gpt-5", "parallel_tool_calls": True})
        retry = UpstreamCompatPolicy().retry_state(state, "Unsupported parameter(s): parallel_tool_calls")
        assert retry is not None
        label, next_state = retry
        self.assertEqual(label, "parallel_tool_calls_rejected")
        self.assertNotIn("parallel_tool_calls", next_state.body)


class ExplicitToolChoiceCompatTests(unittest.TestCase):
    def test_retry_disables_reasoning_for_explicit_tool_choice_in_thinking_mode(self) -> None:
        state = build_initial_reasoning_state(
            {
                "model": "deepseek-v4-flash",
                "tool_choice": {"type": "function", "function": {"name": "shell"}},
            }
        )
        retry = UpstreamCompatPolicy().retry_state(
            state,
            "The tool_choice parameter does not support being set to required or object in thinking mode",
        )
        assert retry is not None
        label, next_state = retry
        self.assertEqual(label, "explicit_tool_choice_disable_reasoning")
        self.assertEqual(next_state.canonical_effort, "none")
        self.assertEqual(next_state.wire_mode, "effort_only")
        self.assertEqual(next_state.body["reasoning_effort"], "none")
        self.assertNotIn("thinking", next_state.body)

    def test_retry_preserves_explicit_reasoning_choice(self) -> None:
        state = build_initial_reasoning_state(
            {
                "model": "deepseek-v4-flash",
                "reasoning_effort": "high",
                "tool_choice": {"type": "function", "function": {"name": "shell"}},
            }
        )
        retry = UpstreamCompatPolicy().retry_state(
            state,
            "The tool_choice parameter does not support being set to required or object in thinking mode",
        )
        self.assertIsNone(retry)

    def test_retry_ignores_non_object_tool_choice(self) -> None:
        state = build_initial_reasoning_state(
            {
                "model": "deepseek-v4-flash",
                "tool_choice": "auto",
            }
        )
        retry = UpstreamCompatPolicy().retry_state(
            state,
            "The tool_choice parameter does not support being set to required or object in thinking mode",
        )
        self.assertIsNone(retry)


class RawThinkingCompatTests(unittest.TestCase):
    def test_provider_default_thinking_can_be_stripped(self) -> None:
        state = build_initial_reasoning_state({"model": "kimi-k2.6"})
        state.body["thinking"] = {"type": "enabled"}
        retry = UpstreamCompatPolicy().retry_state(state, "Unsupported parameter(s): thinking")
        assert retry is not None
        label, next_state = retry
        self.assertEqual(label, "unsupported_thinking_strip_raw_thinking")
        self.assertEqual(next_state.wire_mode, "provider_default")
        self.assertNotIn("thinking", next_state.body)


if __name__ == "__main__":
    unittest.main()
