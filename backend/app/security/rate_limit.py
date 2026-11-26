"""Sliding-window rate limiting shared across API instances.

* ``RedisRateLimiter`` — used whenever ``JOBPILOT_REDIS_URL`` is set (required in production). Each
  check is one atomic Lua script over a sorted set, so concurrent requests hitting different API
  replicas see the same window.
* ``InMemoryRateLimiter`` — single-process fallback for development and tests.

Failure policy when Redis is unreachable: callers choose. Authentication endpoints fail *closed*
(a brute-force window must never open because Redis is down); general API traffic fails *open* so
an infrastructure blip doesn't take the product offline. Both cases are logged.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Protocol

import redis

from app.config import get_settings

log = logging.getLogger("jobpilot.ratelimit")

_SLIDING_WINDOW = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
if redis.call('ZCARD', key) >= limit then
  return 0
end
redis.call('ZADD', key, now, ARGV[4])
redis.call('PEXPIRE', key, window)
return 1
"""


class RateLimiterUnavailable(RuntimeError):
    pass


class RateLimiter(Protocol):
    backend: str

    def allow(self, key: str, limit: int, window_seconds: float = 60.0) -> bool: ...

    def reset(self) -> None: ...


class InMemoryRateLimiter:
    backend = "memory"

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: float = 60.0) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_seconds:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


class RedisRateLimiter:
    backend = "redis"

    def __init__(self, url: str, prefix: str = "jp:rl:") -> None:
        self.prefix = prefix
        self.client = redis.Redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5)
        self._script = self.client.register_script(_SLIDING_WINDOW)

    def allow(self, key: str, limit: int, window_seconds: float = 60.0) -> bool:
        # Server-side clock would be ideal; client time is fine because every replica shares NTP time
        # and the window is coarse (seconds). The random member keeps simultaneous hits distinct.
        now_ms = int(time.time() * 1000)
        try:
            ok = self._script(
                keys=[self.prefix + key],
                args=[now_ms, int(window_seconds * 1000), limit, f"{now_ms}-{secrets.token_hex(4)}"],
            )
        except redis.RedisError as exc:
            raise RateLimiterUnavailable(str(exc.__class__.__name__)) from exc
        return bool(ok)

    def ping(self) -> bool:
        try:
            return bool(self.client.ping())
        except redis.RedisError:
            return False

    def reset(self) -> None:
        for k in self.client.scan_iter(match=self.prefix + "*"):
            self.client.delete(k)


class _LimiterHandle:
    """Resolves the configured backend lazily (settings can change between tests)."""

    def __init__(self) -> None:
        self._impl: RateLimiter | None = None
        self._url: str | None = None

    def _get(self) -> RateLimiter:
        url = get_settings().redis_url
        if self._impl is None or url != self._url:
            self._url = url
            self._impl = RedisRateLimiter(url) if url else InMemoryRateLimiter()
        return self._impl

    @property
    def backend(self) -> str:
        return self._get().backend

    def allow(self, key: str, limit: int, window_seconds: float = 60.0, *, fail_closed: bool = False) -> bool:
        try:
            return self._get().allow(key, limit, window_seconds)
        except RateLimiterUnavailable as exc:
            log.error("rate_limiter_unavailable", extra={"error": str(exc), "fail_closed": fail_closed})
            return not fail_closed

    def reset(self) -> None:
        self._get().reset()

    def healthy(self) -> bool:
        impl = self._get()
        return impl.ping() if isinstance(impl, RedisRateLimiter) else True


rate_limiter = _LimiterHandle()
