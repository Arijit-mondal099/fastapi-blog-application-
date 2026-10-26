"""Redis-backed fixed-window rate limiting for auth endpoints."""

from typing import cast

from fastapi import Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.errors import RateLimitExceeded, RateLimitUnavailable

RATE_LIMIT_MAX_REQUESTS = 60
RATE_LIMIT_WINDOW_SECONDS = 60


def client_ip(request: Request, trusted_proxy_count: int) -> str:
    """Resolve the client address, trusting X-Forwarded-For only when configured."""
    if trusted_proxy_count > 0:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            parts = [part.strip() for part in forwarded.split(",") if part.strip()]
            if len(parts) >= trusted_proxy_count:
                return parts[-trusted_proxy_count]

    if request.client is not None:
        return request.client.host
    return "unknown"


async def enforce_auth_rate_limit(request: Request) -> None:
    """Apply the auth rate limit, failing closed if Redis is unavailable."""
    settings = get_settings()
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise RateLimitUnavailable()

    key = f"rate_limit:auth:{client_ip(request, settings.trusted_proxy_count)}:{request.url.path}"
    client = cast(Redis, redis)
    try:
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, RATE_LIMIT_WINDOW_SECONDS)
    except (RedisError, OSError) as exc:
        raise RateLimitUnavailable() from exc

    if count > RATE_LIMIT_MAX_REQUESTS:
        raise RateLimitExceeded()
