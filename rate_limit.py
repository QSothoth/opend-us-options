#!/usr/bin/env python3
"""Sliding-window rate limiter for OpenD ``request_history_kline``.

Official limit is 60 calls / 30s. We default to **50 calls / 30s** to stay
safely under the cap. Thread-safe; ``acquire()`` blocks until a slot is free,
``try_acquire()`` returns immediately.
"""

from __future__ import annotations

import threading
import time
from collections import deque


class RateLimitError(RuntimeError):
    """Raised when no capacity is available and the caller did not want to block."""


class SlidingWindowRateLimiter:
    """Sliding-window call limiter.

    Keeps the timestamps of calls made within the last ``window_seconds`` and
    blocks new callers until enough old calls have left the window.
    """

    def __init__(self, max_calls: int = 50, window_seconds: float = 30.0, name: str = "history_kline"):
        if max_calls <= 0:
            raise ValueError("max_calls must be > 0")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self.max_calls = int(max_calls)
        self.window_seconds = float(window_seconds)
        self.name = name
        self._calls: deque = deque()
        self._lock = threading.Lock()

    def _trim(self, now: float) -> None:
        horizon = now - self.window_seconds
        while self._calls and self._calls[0] <= horizon:
            self._calls.popleft()

    def remaining(self) -> int:
        """Number of calls still available in the current window."""
        with self._lock:
            self._trim(time.monotonic())
            return self.max_calls - len(self._calls)

    def wait_seconds(self) -> float:
        """Seconds until the next call would be allowed (0 if allowed now)."""
        with self._lock:
            now = time.monotonic()
            self._trim(now)
            if len(self._calls) < self.max_calls:
                return 0.0
            return max(0.0, self.window_seconds - (now - self._calls[0]) + 0.01)

    def try_acquire(self) -> bool:
        """Acquire a slot without blocking. Returns True on success."""
        with self._lock:
            now = time.monotonic()
            self._trim(now)
            if len(self._calls) < self.max_calls:
                self._calls.append(now)
                return True
            return False

    def acquire(self) -> None:
        """Block until a slot is available, then consume it."""
        while True:
            with self._lock:
                now = time.monotonic()
                self._trim(now)
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return
                wait = max(0.0, self.window_seconds - (now - self._calls[0]) + 0.01)
            time.sleep(wait)


def history_kline_limiter(max_calls: int = 50, window_seconds: float = 30.0) -> SlidingWindowRateLimiter:
    """Convenience factory for the ``request_history_kline`` default (50/30s)."""
    return SlidingWindowRateLimiter(
        max_calls=max_calls,
        window_seconds=window_seconds,
        name="request_history_kline",
    )


if __name__ == "__main__":
    rl = SlidingWindowRateLimiter(max_calls=3, window_seconds=0.2)
    for _ in range(6):
        rl.acquire()
    assert rl.remaining() >= 0
    print("rate_limit smoke test OK")
