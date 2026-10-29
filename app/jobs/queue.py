"""ARQ queue access shared by HTTP requests and background jobs."""

from typing import Any, Protocol, cast

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings
from fastapi import Request

from app.core.config import Settings


class JobQueue(Protocol):
    """Minimal enqueue interface used by services."""

    async def enqueue(
        self,
        function: str,
        *args: Any,
        request_id: str | None = None,
    ) -> None:
        """Enqueue one job, propagating the originating request ID."""


class ArqJobQueue:
    """ARQ-backed job queue."""

    def __init__(self, redis: ArqRedis) -> None:
        self._redis = redis

    async def enqueue(
        self,
        function: str,
        *args: Any,
        request_id: str | None = None,
    ) -> None:
        await self._redis.enqueue_job(function, *args, request_id=request_id)

    async def aclose(self) -> None:
        """Release the underlying Redis pool."""
        await self._redis.aclose()


class NoopJobQueue:
    """Queue used when no ARQ pool is configured, for example in unit tests."""

    async def enqueue(
        self,
        function: str,
        *args: Any,
        request_id: str | None = None,
    ) -> None:
        return None


async def create_job_queue(settings: Settings) -> ArqJobQueue:
    """Create the process-wide ARQ Redis pool."""
    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return ArqJobQueue(redis)


def get_job_queue(request: Request) -> JobQueue:
    """Return the application's job queue, or a no-op queue when unset."""
    queue = getattr(request.app.state, "job_queue", None)
    if queue is None:
        return NoopJobQueue()
    return cast(JobQueue, queue)
