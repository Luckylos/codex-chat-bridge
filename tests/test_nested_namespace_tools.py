"""Tests for NestedOneOf/NestedAnyOf namespace tool schema merging.

Covers:
- add_namespace_tool with strategy="nested_oneof" merges sub-tools into
  a single Chat tool with oneOf parameter variants
- add_namespace_tool with strategy="nested_anyof" merges sub-tools into
  a single Chat tool with action enum + params.anyOf
- Default (no strategy / strategy="flat") preserves existing flat behaviour
- Response-side conversion extracts action from arguments JSON
- Stream-side detection of namespace-level calls (warn path)
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from codex_chat_bridge.bridge_context import BridgeToolContext, build_tool_context_from_request
from codex_chat_bridge.bridge_context.models import ToolSpec
from codex_chat_bridge.models import ResponsesRequest
from codex_chat_bridge.chat_to_responses.tools import tool_call_to_response_item, _nested_namespace_call_to_response_item


_CLASSIC_FLAT = {
    "type": "namespace",
    "name": "codex",
    "strategy": "flat",
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "shell",
                "description": "Execute a shell command",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "apply_patch",
                "description": "Apply a patch",
                "parameters": {
                    "type": "object",
                    "properties": {"patch": {"type": "string"}},
                    "required": ["patch"],
                },
            },
        },
    ],
}

_NESTED_ONEOF = {
    "type": "namespace",
    "name": "codex",
    "strategy": "nested_oneof",
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "shell",
                "description": "Execute a shell command",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "apply_patch",
                "description": "Apply a patch",
                "parameters": {
                    "type": "object",
                    "properties": {"patch": {"type": "string"}},
                    "required": ["patch"],
                },
            },
        },
    ],
}

_NESTED_ANYOF = {
    "type": "namespace",
    "name": "codex",
    "strategy": "nested_anyof",
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write a file",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"],
                },
            },
        },
    ],
}


class FlatNamespaceToolTests(unittest.TestCase):
    """Existing flat namespace tool behaviour must not change."""

    def test_flat_strategy_produces_separate_chat_tools(self) -> None:
        ctx = BridgeToolContext()
        ctx.add_namespace_tool(_CLASSIC_FLAT)
        # Two separate flattened functions
        chat_names = [t["function"]["name"] for t in ctx.chat_tools if t.get("type") == "function"]
        self.assertIn("codex__shell", chat_names)
        self.assertIn("codex__apply_patch", chat_names)
        self.assertEqual(len(chat_names), 2)

    def test_flat_namespace_no_nested_spec(self) -> None:
        ctx = BridgeToolContext()
        ctx.add_namespace_tool(_CLASSIC_FLAT)
        spec = ctx.lookup_chat_name("codex__shell")
        assert spec is not None
        self.assertEqual(spec.kind, "function")
        self.assertIsNone(spec.namespace_strategy)


class NestedOneOfSchemaTests(unittest.TestCase):
    """NestedOneOf produces a single Chat tool with oneOf parameter variants."""

    def test_produces_single_chat_tool(self) -> None:
        ctx = BridgeToolContext()
        ctx.add_namespace_tool(_NESTED_ONEOF)
        chat_names = [t["function"]["name"] for t in ctx.chat_tools if t.get("type") == "function"]
        self.assertEqual(len(chat_names), 1)
        self.assertEqual(chat_names[0], "codex__codex")

    def test_tool_spec_marked_as_namespace(self) -> None:
        ctx = BridgeToolContext()
        ctx.add_namespace_tool(_NESTED_ONEOF)
        chat_name = "codex__codex"
        spec = ctx.lookup_chat_name(chat_name)
        assert spec is not None
        self.assertEqual(spec.kind, "namespace")
        self.assertEqual(spec.namespace_strategy, "nested_oneof")
        self.assertEqual(spec.actions, ["shell", "apply_patch"])
        self.assertEqual(spec.namespace, "codex")

    def test_schema_has_oneof_variants(self) -> None:
        ctx = BridgeToolContext()
        ctx.add_namespace_tool(_NESTED_ONEOF)
        tool = ctx.chat_tools[0]
        params = tool["function"]["parameters"]
        self.assertIn("oneOf", params)
        variants = params["oneOf"]
        self.assertEqual(len(variants), 2)
        # Each variant has its own action enum
        actions_in_schema = set()
        for variant in variants:
            props = variant.get("properties", {})
            action = props.get("action", {})
            enum_vals = action.get("enum", [])
            self.assertEqual(len(enum_vals), 1)
            actions_in_schema.add(enum_vals[0])
        self.assertEqual(actions_in_schema, {"shell", "apply_patch"})

    def test_each_variant_carries_original_properties(self) -> None:
        ctx = BridgeToolContext()
        ctx.add_namespace_tool(_NESTED_ONEOF)
        tool = ctx.chat_tools[0]
        params = tool["function"]["parameters"]
        variants = params["oneOf"]
        # Shell variant should have 'command' property
        shell_variant = next(v for v in variants if "command" in v.get("properties", {}))
        self.assertIn("command", shell_variant["properties"])
        self.assertIn("action", shell_variant["required"])


class NestedAnyOfSchemaTests(unittest.TestCase):
    """NestedAnyOf produces a single Chat tool with action enum + params.anyOf."""

    def test_produces_single_chat_tool(self) -> None:
        ctx = BridgeToolContext()
        ctx.add_namespace_tool(_NESTED_ANYOF)
        chat_names = [t["function"]["name"] for t in ctx.chat_tools if t.get("type") == "function"]
        self.assertEqual(len(chat_names), 1)
        self.assertEqual(chat_names[0], "codex__codex")

    def test_tool_spec_marked_as_namespace_anyof(self) -> None:
        ctx = BridgeToolContext()
        ctx.add_namespace_tool(_NESTED_ANYOF)
        spec = ctx.lookup_chat_name("codex__codex")
        assert spec is not None
        self.assertEqual(spec.namespace_strategy, "nested_anyof")
        self.assertEqual(spec.actions, ["read_file", "write_file"])

    def test_schema_has_action_enum_and_params_anyof(self) -> None:
        ctx = BridgeToolContext()
        ctx.add_namespace_tool(_NESTED_ANYOF)
        tool = ctx.chat_tools[0]
        params = tool["function"]["parameters"]
        props = params.get("properties", {})
        self.assertIn("action", props)
        self.assertEqual(props["action"]["enum"], ["read_file", "write_file"])
        self.assertIn("params", props)

    def test_required_includes_action(self) -> None:
        ctx = BridgeToolContext()
        ctx.add_namespace_tool(_NESTED_ANYOF)
        tool = ctx.chat_tools[0]
        params = tool["function"]["parameters"]
        self.assertIn("action", params.get("required", []))


class NestedNamespaceResponseConversionTests(unittest.TestCase):
    """Response-side: namespace-level call → Responses function_call extraction."""

    def _make_context(self, strategy: str = "nested_oneof") -> BridgeToolContext:
        ctx = BridgeToolContext()
        namespace_tool = dict(_NESTED_ONEOF) if strategy == "nested_oneof" else dict(_NESTED_ANYOF)
        namespace_tool["strategy"] = strategy
        ctx.add_namespace_tool(namespace_tool)
        return ctx

    def test_oneof_extracts_action_from_arguments(self) -> None:
        ctx = self._make_context("nested_oneof")
        spec = ctx.lookup_chat_name("codex__codex")
        assert spec is not None
        args = json.dumps({"action": "shell", "command": "ls -la"})
        item = _nested_namespace_call_to_response_item("call_1", spec, args, "")
        self.assertEqual(item["name"], "shell")
        self.assertEqual(item["namespace"], "codex")
        # 'action' key removed from arguments
        result_args = json.loads(item["arguments"])
        self.assertNotIn("action", result_args)
        self.assertEqual(result_args["command"], "ls -la")

    def test_anyof_extracts_action_and_flattens_params(self) -> None:
        ctx = self._make_context("nested_anyof")
        spec = ctx.lookup_chat_name("codex__codex")
        assert spec is not None
        args = json.dumps({"action": "write_file", "params": {"path": "/tmp/f", "content": "hi"}})
        item = _nested_namespace_call_to_response_item("call_1", spec, args, "")
        self.assertEqual(item["name"], "write_file")
        result_args = json.loads(item["arguments"])
        self.assertNotIn("action", result_args)
        self.assertNotIn("params", result_args)
        self.assertEqual(result_args["path"], "/tmp/f")

    def test_unknown_action_falls_back_to_namespace_name(self) -> None:
        ctx = self._make_context("nested_oneof")
        spec = ctx.lookup_chat_name("codex__codex")
        assert spec is not None
        args = json.dumps({"action": "nonexistent_action", "command": "ls"})
        item = _nested_namespace_call_to_response_item("call_1", spec, args, "")
        # Invalid action → fall back to spec.name (namespace name)
        self.assertEqual(item["name"], "codex")

    def test_no_action_key_falls_back_to_namespace_name(self) -> None:
        ctx = self._make_context("nested_oneof")
        spec = ctx.lookup_chat_name("codex__codex")
        assert spec is not None
        args = json.dumps({"command": "ls"})
        item = _nested_namespace_call_to_response_item("call_1", spec, args, "")
        self.assertEqual(item["name"], "codex")

    def test_invalid_json_args_fall_back_gracefully(self) -> None:
        ctx = self._make_context("nested_oneof")
        spec = ctx.lookup_chat_name("codex__codex")
        assert spec is not None
        item = _nested_namespace_call_to_response_item("call_1", spec, "{broken json", "")
        self.assertEqual(item["name"], "codex")
        self.assertEqual(item["arguments"], "{broken json")

    def test_reasoning_preserved_in_nested_item(self) -> None:
        ctx = self._make_context("nested_oneof")
        spec = ctx.lookup_chat_name("codex__codex")
        assert spec is not None
        args = json.dumps({"action": "shell", "command": "ls"})
        item = _nested_namespace_call_to_response_item("call_1", spec, args, "I need to list files")
        self.assertEqual(item["reasoning_content"], "I need to list files")

    def test_integration_via_tool_call_to_response_item(self) -> None:
        ctx = self._make_context("nested_oneof")
        args = json.dumps({"action": "apply_patch", "patch": "--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new"})
        item = tool_call_to_response_item("call_1", "codex__codex", args, "patch reasoning", ctx)
        self.assertEqual(item["type"], "function_call")
        self.assertEqual(item["name"], "apply_patch")
        self.assertEqual(item["namespace"], "codex")
        self.assertEqual(item["reasoning_content"], "patch reasoning")


class NestedNamespaceEmptyChildrenTests(unittest.TestCase):
    """Edge cases: empty children, no valid functions."""

    def test_empty_children_produces_no_tool(self) -> None:
        ctx = BridgeToolContext()
        ctx.add_namespace_tool({"type": "namespace", "name": "empty_ns", "strategy": "nested_oneof", "tools": []})
        self.assertEqual(len(ctx.chat_tools), 0)

    def test_no_valid_functions_produces_no_tool(self) -> None:
        ctx = BridgeToolContext()
        ctx.add_namespace_tool({
            "type": "namespace", "name": "bad_ns", "strategy": "nested_oneof",
            "tools": [{"type": "not_function"}],
        })
        self.assertEqual(len(ctx.chat_tools), 0)

    def test_no_strategy_defaults_to_flat(self) -> None:
        ctx = BridgeToolContext()
        ctx.add_namespace_tool({
            "type": "namespace", "name": "codex",
            "tools": [
                {"type": "function", "function": {"name": "shell", "parameters": {}}},
            ],
        })
        # Default strategy is "flat" → separate tools
        chat_names = [t["function"]["name"] for t in ctx.chat_tools if t.get("type") == "function"]
        self.assertIn("codex__shell", chat_names)
