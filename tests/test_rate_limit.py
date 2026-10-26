"""Redis-backed auth rate limiting."""

import httpx

from tests.helpers import FakeRedis


async def test_rate_limit_returns_429_after_60_requests(
    client: httpx.AsyncClient,
) -> None:
    for _ in range(60):
        response = await client.get("/auth/google/login")
        assert response.status_code == 302

    limited = await client.get("/auth/google/login")
    assert limited.status_code == 429


async def test_rate_limit_returns_503_when_redis_is_unreachable(
    client: httpx.AsyncClient, fake_redis: FakeRedis
) -> None:
    fake_redis.fail = True

    response = await client.get("/auth/google/login")

    assert response.status_code == 503
