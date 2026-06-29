"""Re-export facade for chat_to_responses submodules.

All consumers continue to import from .common — the real logic
now lives in text.py, annotations.py, and inline_think.py.
"""
from __future__ import annotations

from .text import extract_reasoning_text, output_text_from_parts
from .annotations import extract_message_annotations, message_content_parts
from .inline_think import split_inline_think, could_be_partial_think_open

__all__ = [
    "extract_reasoning_text",
    "extract_message_annotations",
    "message_content_parts",
    "output_text_from_parts",
    "split_inline_think",
    "could_be_partial_think_open",
]
