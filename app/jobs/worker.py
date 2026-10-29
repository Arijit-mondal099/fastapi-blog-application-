"""ARQ worker configuration: registered jobs, retries, and cron schedules."""

import logging
from typing import Any

from arq import cron, func
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import dispose_engine
from app.jobs.auth_jobs import purge_expired_refresh_tokens
from app.jobs.media_jobs import (
    DELETE_REMOTE_MEDIA_MAX_TRIES,
    VERIFY_MEDIA_MAX_TRIES,
    delete_remote_media,
    sweep_orphans,
    verify_media,
)

logger = logging.getLogger(__name__)

CRON_SCHEDULES = {
    "sweep_orphans": "hourly at minute 0",
    "purge_expired_refresh_tokens": "daily at 03:00",
}


async def startup(ctx: dict[str, Any]) -> None:
    """Configure logging and report the worker's registrations."""
    configure_logging()
    get_settings().validate_startup()
    logger.info(
        "ARQ worker started",
        extra={
            "jobs": [
                {
                    "name": "delete_remote_media",
                    "max_tries": DELETE_REMOTE_MEDIA_MAX_TRIES,
                },
                {"name": "verify_media", "max_tries": VERIFY_MEDIA_MAX_TRIES},
                {"name": "sweep_orphans", "max_tries": 1},
                {"name": "purge_expired_refresh_tokens", "max_tries": 1},
            ],
            "cron_jobs": [
                {"name": name, "schedule": schedule} for name, schedule in CRON_SCHEDULES.items()
            ],
        },
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    """Release the database engine used by job functions."""
    await dispose_engine()


class WorkerSettings:
    """Settings consumed by the ARQ CLI."""

    functions: list[Any] = [
        func(delete_remote_media, max_tries=DELETE_REMOTE_MEDIA_MAX_TRIES),
        func(verify_media, max_tries=VERIFY_MEDIA_MAX_TRIES),
        func(sweep_orphans, max_tries=1),
        func(purge_expired_refresh_tokens, max_tries=1),
    ]
    cron_jobs: list[Any] = [
        cron(sweep_orphans, name="sweep_orphans", minute=0, second=0, max_tries=1),
        cron(
            purge_expired_refresh_tokens,
            name="purge_expired_refresh_tokens",
            hour=3,
            minute=0,
            second=0,
            max_tries=1,
        ),
    ]
    redis_settings: RedisSettings = RedisSettings.from_dsn(get_settings().redis_url)
    on_startup = startup
    on_shutdown = shutdown
