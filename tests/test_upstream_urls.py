from __future__ import annotations

import unittest

from codex_chat_bridge.config import Settings
from codex_chat_bridge.upstream import UpstreamClient


class UpstreamUrlDerivationTests(unittest.TestCase):
    """Cover UpstreamClient._chat_completions_url / _models_url URL logic.

    These pure-functions translate a user-supplied base URL (e.g.
    "https://newapi.example.com/v1") into the concrete upstream endpoints.
    """

    def _client(self, base_url: str) -> UpstreamClient:
        return UpstreamClient(Settings(
            upstream_base_url=base_url,
            upstream_api_key="test-key",
            upstream_timeout_seconds=30,
        ))

    # ---- _chat_completions_url ----

    def test_chat_url_with_v1_trailing(self) -> None:
        url = self._client("https://up.example.com/v1")._chat_completions_url()
        self.assertEqual(url, "https://up.example.com/v1/chat/completions")

    def test_chat_url_with_full_path(self) -> None:
        url = self._client("https://up.example.com/v1/chat/completions")._chat_completions_url()
        self.assertEqual(url, "https://up.example.com/v1/chat/completions")

    def test_chat_url_without_v1(self) -> None:
        url = self._client("https://up.example.com")._chat_completions_url()
        self.assertEqual(url, "https://up.example.com/v1/chat/completions")

    def test_chat_url_with_chat_only(self) -> None:
        url = self._client("https://up.example.com/chat/completions")._chat_completions_url()
        self.assertEqual(url, "https://up.example.com/chat/completions")

    def test_chat_url_trailing_slash_stripped(self) -> None:
        url = self._client("https://up.example.com/v1/")._chat_completions_url()
        self.assertEqual(url, "https://up.example.com/v1/chat/completions")

    def test_chat_url_empty_raises(self) -> None:
        client = self._client("https://up.example.com")
        client._settings.upstream_base_url = ""
        with self.assertRaisesRegex(RuntimeError, "BRIDGE_UPSTREAM_BASE_URL is empty"):
            client._chat_completions_url()

    # ---- _models_url ----

    def test_models_url_with_v1_trailing(self) -> None:
        url = self._client("https://up.example.com/v1")._models_url()
        self.assertEqual(url, "https://up.example.com/v1/models")

    def test_models_url_with_full_path(self) -> None:
        url = self._client("https://up.example.com/v1/models")._models_url()
        self.assertEqual(url, "https://up.example.com/v1/models")

    def test_models_url_without_v1(self) -> None:
        url = self._client("https://up.example.com")._models_url()
        self.assertEqual(url, "https://up.example.com/v1/models")

    def test_models_url_with_models_only(self) -> None:
        url = self._client("https://up.example.com/models")._models_url()
        self.assertEqual(url, "https://up.example.com/models")

    def test_models_url_trailing_slash_stripped(self) -> None:
        url = self._client("https://up.example.com/v1/")._models_url()
        self.assertEqual(url, "https://up.example.com/v1/models")

    def test_models_url_empty_raises(self) -> None:
        client = self._client("https://up.example.com")
        client._settings.upstream_base_url = ""
        with self.assertRaisesRegex(RuntimeError, "BRIDGE_UPSTREAM_BASE_URL is empty"):
            client._models_url()
