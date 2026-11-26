import os

import pytest

from app.security.rate_limit import InMemoryRateLimiter, RedisRateLimiter, _LimiterHandle

REDIS_URL = os.environ.get("JOBPILOT_TEST_REDIS_URL")  # e.g. redis://localhost:6379/15
needs_redis = pytest.mark.skipif(not REDIS_URL, reason="JOBPILOT_TEST_REDIS_URL not set")


def test_memory_limiter_window():
    rl = InMemoryRateLimiter()
    assert [rl.allow("k", 3) for _ in range(4)] == [True, True, True, False]
    assert rl.allow("other", 3)


@needs_redis
def test_redis_limit_is_shared_across_instances():
    # Two limiter objects with separate connections stand in for two API replicas.
    a, b = RedisRateLimiter(REDIS_URL, prefix="jp:test:"), RedisRateLimiter(REDIS_URL, prefix="jp:test:")
    a.reset()
    results = [(a if i % 2 else b).allow("login:ip", 5) for i in range(7)]
    assert results == [True] * 5 + [False] * 2
    assert a.allow("login:other", 5)
    a.reset()


@needs_redis
def test_redis_window_expires():
    rl = RedisRateLimiter(REDIS_URL, prefix="jp:test:")
    rl.reset()
    assert rl.allow("w", 1, window_seconds=0.3)
    assert not rl.allow("w", 1, window_seconds=0.3)
    import time

    time.sleep(0.4)
    assert rl.allow("w", 1, window_seconds=0.3)
    rl.reset()


def test_unreachable_redis_fails_closed_for_auth_and_open_for_api(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "redis_url", "redis://127.0.0.1:1/0")  # nothing listens on port 1
    handle = _LimiterHandle()
    assert handle.backend == "redis"
    assert handle.allow("auth", 10, fail_closed=True) is False
    assert handle.allow("api", 10) is True
    assert handle.healthy() is False


def test_production_requires_redis(monkeypatch):
    from cryptography.fernet import Fernet
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError, match="REDIS_URL"):
        Settings(
            env="production",
            secret_key="Zq7xW2pL9vR4tY8uK3mN6bH1cJ5dF0gS-eA_x9",
            encryption_key=Fernet.generate_key().decode(),
            cookie_secure=True,
            redis_url=None,
        )


def test_platform_url_forms_are_normalised():
    from app.config import Settings

    for raw in ("postgres://u:p@db:5432/jp", "postgresql://u:p@db:5432/jp"):
        assert Settings(database_url=raw).database_url == "postgresql+psycopg://u:p@db:5432/jp"
    assert Settings(database_url="sqlite:///x.db").database_url == "sqlite:///x.db"
    assert Settings(redis_url=None, redis_host="kv-internal", redis_port=6380).redis_url == "redis://kv-internal:6380/0"
    assert Settings(redis_url="redis://a:1/2", redis_host="ignored").redis_url == "redis://a:1/2"
