"""ARQ worker configuration. Jobs and cron schedules are added in later phases."""

from typing import Any

from arq.connections import RedisSettings

from app.core.config import get_settings


async def _worker_noop() -> None:
    """Placeholder required by ARQ, which refuses to start without a registered function."""


class WorkerSettings:
    """Minimal worker settings for the Phase 0 skeleton."""

    functions: list[Any] = [_worker_noop]
    cron_jobs: list[Any] = []
    redis_settings: RedisSettings = RedisSettings.from_dsn(get_settings().redis_url)
