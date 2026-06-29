"""Buffered Chat-Completions-to-Responses restore package.

Keeps non-streaming response reconstruction split into content extraction,
tool-call restoration, and response-envelope assembly, while exporting a
single stable entrypoint for callers.
"""

from .response import chat_text_to_responses

# Symmetric entrypoint alias — matches responses_to_chat.convert()
convert = chat_text_to_responses

__all__ = ["chat_text_to_responses", "convert"]
