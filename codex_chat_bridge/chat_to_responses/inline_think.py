"""Inline think reasoning detection and splitting.

Some models (kimi, GLM, DeepSeek V3) embed reasoning inside
the content field as think tags instead of using
the reasoning_content field.  This module extracts that
inline reasoning so the bridge can emit proper Responses
reasoning items.
"""
from __future__ import annotations

import re
from typing import NamedTuple

# Both <think> and <thinking> are accepted as opening tags.
_THINK_OPEN_RE = re.compile(r"<(?:think|thinking)>", re.IGNORECASE)
_THINK_CLOSE_RE = re.compile(r"</(?:think|thinking)>", re.IGNORECASE)
_TAG_RE = re.compile(r"</?(?:think|thinking)>", re.IGNORECASE)

# Canonical tag strings for partial-match detection during streaming
_OPEN_TAG = "<think>"
_OPEN_TAG_ALT = "<thinking>"


class InlineThinkResult(NamedTuple):
    """Result of splitting a string that may contain an inline think block."""
    reasoning: str | None
    """The extracted reasoning text (inside think tags), or None if no block found."""
    answer: str
    """The remaining visible answer text (outside think tags)."""


def split_inline_think(text: str) -> InlineThinkResult:
    """Split a complete string that may contain one leading think block.

    Returns (reasoning, answer).  If no matching block is found, reasoning
    is None and answer is the original text.
    """
    open_m = _THINK_OPEN_RE.search(text)
    if not open_m:
        return InlineThinkResult(None, text)

    close_m = _THINK_CLOSE_RE.search(text, open_m.end())
    if not close_m:
        # Unclosed think block: treat everything after the open tag as reasoning
        reasoning = _strip_think_tags(text[open_m.end():])
        return InlineThinkResult(reasoning or None, "")

    reasoning = text[open_m.end():close_m.start()]
    answer = text[:open_m.start()] + text[close_m.end():]
    return InlineThinkResult(reasoning.strip() or None, answer.strip())


def _strip_think_tags(text: str) -> str:
    """Remove all <think></think>/<thinking></thinking> tags from text, preserving inner content."""
    return _TAG_RE.sub("", text)


def could_be_partial_think_open(buffer: str) -> bool:
    """Return True if buffer is a partial prefix of open-tag string.

    Used in streaming to decide whether to accumulate more bytes
    before emitting content.
    """
    b = buffer.lstrip().lower()
    if not b:
        return False
    return _OPEN_TAG.startswith(b) or _OPEN_TAG_ALT.startswith(b)
