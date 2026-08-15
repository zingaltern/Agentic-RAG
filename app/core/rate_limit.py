"""进程内滑动窗口速率限制器（C4，V5.1 并发加固；纯标准库）。

- 每 key（默认按 IP）在固定窗口内的命中次数上限，超限拒绝。
- 进程内实现：Web 多 worker 部署时各进程独立计数（局限，MVP 不强行上 Redis）。
"""
from __future__ import annotations

import threading
import time
from collections import deque


class SlidingWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: int = 60):
        if limit <= 0:
            raise ValueError("limit 必须 > 0")
        self._limit = limit
        self._window = window_seconds
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        """窗口内放行一次并记录；超限返回 False。"""
        with self._lock:
            now = time.time()
            window_start = now - self._window
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] < window_start:
                hits.popleft()
            if len(hits) >= self._limit:
                return False
            hits.append(now)
            return True
