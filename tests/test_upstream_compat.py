from __future__ import annotations

import unittest

from codex_chat_bridge.reasoning_policy import build_initial_reasoning_state
from codex_chat_bridge.upstream_compat import UpstreamCompatPolicy


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


if __name__ == "__main__":
    unittest.main()
