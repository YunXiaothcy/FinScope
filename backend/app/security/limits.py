"""Bounded, atomic, single-process request budgets; production uses one worker."""

from collections import OrderedDict, deque
from threading import Lock
from time import monotonic

from fastapi import HTTPException


class WindowLimiter:
    def __init__(self, limit: int, seconds: int, max_keys: int = 10000):
        self.limit, self.seconds, self.max_keys = limit, seconds, max_keys
        self.entries: OrderedDict[str, deque[float]] = OrderedDict()
        self.lock = Lock()

    def consume(self, key: str):
        with self.lock:
            now = monotonic()
            cutoff = now - self.seconds
            while self.entries:
                oldest = next(iter(self.entries))
                if self.entries[oldest][-1] > cutoff:
                    break
                self.entries.popitem(last=False)
            entries = self.entries.get(key)
            if entries is None:
                if len(self.entries) >= self.max_keys:
                    raise HTTPException(429, "服务繁忙，请稍后重试")
                entries = self.entries[key] = deque()
            while entries and entries[0] <= cutoff:
                entries.popleft()
            if len(entries) >= self.limit:
                raise HTTPException(429, "请求过于频繁，请稍后重试")
            entries.append(now)
            self.entries.move_to_end(key)

    def clear(self, key: str | None = None):
        with self.lock:
            if key is None:
                self.entries.clear()
            else:
                self.entries.pop(key, None)
