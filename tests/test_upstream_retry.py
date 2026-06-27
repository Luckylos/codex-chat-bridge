from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from codex_chat_bridge.upstream import _backoff_delay, _retryable_status


class RetryLogicTests(unittest.TestCase):
    def test_retryable_status_429(self) -> None:
        self.assertTrue(_retryable_status(429))

    def test_retryable_status_500(self) -> None:
        self.assertTrue(_retryable_status(500))

    def test_retryable_status_503(self) -> None:
        self.assertTrue(_retryable_status(503))

    def test_retryable_status_200_is_not_retryable(self) -> None:
        self.assertFalse(_retryable_status(200))

    def test_retryable_status_400_is_not_retryable(self) -> None:
        self.assertFalse(_retryable_status(400))

    def test_backoff_delay_increases(self) -> None:
        d0 = _backoff_delay(0, base=0.5)
        d1 = _backoff_delay(1, base=0.5)
        d2 = _backoff_delay(2, base=0.5)
        # Base delay should increase
        self.assertLess(d0, d1)
        self.assertLess(d1, d2)

    def test_backoff_delay_capped(self) -> None:
        d = _backoff_delay(10, base=1, max_delay=30)
        self.assertAlmostEqual(d, 30.0, delta=1.5)  # 30 + jitter 0-1

    def test_backoff_delay_has_jitter(self) -> None:
        delays = {_backoff_delay(0, base=1, max_delay=10) for _ in range(20)}
        # At least some variation from jitter
        self.assertGreater(len(delays), 1)
