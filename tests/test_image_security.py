from __future__ import annotations

import unittest

from codex_chat_bridge.responses_to_chat.errors import UnsupportedResponsesInputItemError
from codex_chat_bridge.responses_to_chat.media import chat_image_part_from_input_item, is_safe_image_url


class ImageUrlSecurityTests(unittest.TestCase):
    def test_safe_https_url_is_allowed(self) -> None:
        self.assertTrue(is_safe_image_url("https://example.com/image.png"))

    def test_safe_data_url_is_allowed(self) -> None:
        self.assertTrue(is_safe_image_url("data:image/png;base64,iVBORw0KGgo="))

    def test_unsafe_file_url_is_rejected(self) -> None:
        self.assertFalse(is_safe_image_url("file:///etc/passwd"))

    def test_unsafe_http_url_is_rejected(self) -> None:
        self.assertFalse(is_safe_image_url("http://169.254.169.254/latest/meta-data/"))

    def test_unsafe_ftp_url_is_rejected(self) -> None:
        self.assertFalse(is_safe_image_url("ftp://internal-server/file"))

    def test_empty_url_is_rejected(self) -> None:
        self.assertFalse(is_safe_image_url(""))

    def test_none_is_rejected(self) -> None:
        self.assertFalse(is_safe_image_url(None))

    def test_non_string_is_rejected(self) -> None:
        self.assertFalse(is_safe_image_url(123))  # type: ignore[arg-type]

    def test_chat_image_part_rejects_http_url(self) -> None:
        with self.assertRaises(UnsupportedResponsesInputItemError):
            chat_image_part_from_input_item({
                "type": "input_image",
                "image_url": {"url": "http://internal-server/config"},
            })

    def test_chat_image_part_accepts_https_url(self) -> None:
        result = chat_image_part_from_input_item({
            "type": "input_image",
            "image_url": "https://example.com/photo.jpg",
        })
        self.assertEqual(result["image_url"]["url"], "https://example.com/photo.jpg")

    def test_chat_image_part_accepts_data_url(self) -> None:
        result = chat_image_part_from_input_item({
            "type": "input_image",
            "image_url": "data:image/png;base64,dGVzdA==",
        })
        self.assertEqual(result["image_url"]["url"], "data:image/png;base64,dGVzdA==")
