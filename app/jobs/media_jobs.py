"""ARQ jobs for media verification and deletion."""

import logging
from typing import Any
from uuid import UUID

from arq import Retry
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_request_id, reset_request_id, set_request_id
from app.db.session import get_session_factory
from app.integrations.imagekit import ImageKitClientError, get_imagekit_client
from app.jobs.queue import ArqJobQueue, JobQueue, NoopJobQueue
from app.repositories.media import MediaRepository
from app.repositories.posts import PostRepository
from app.services.media import MediaService

DELETE_REMOTE_MEDIA_MAX_TRIES = 5
VERIFY_MEDIA_MAX_TRIES = 3
RETRY_BASE_DELAY_SECONDS = 2.0
RETRY_MAX_DELAY_SECONDS = 60.0

logger = logging.getLogger(__name__)


def _retry_delay(ctx: dict[str, Any], max_tries: int) -> float | None:
    attempt = int(ctx.get("job_try", 1))
    if attempt >= max_tries:
        return None
    delay: float = min(
        RETRY_MAX_DELAY_SECONDS,
        RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)),
    )
    return delay


def _build_media_service(session: AsyncSession, ctx: dict[str, Any]) -> MediaService:
    redis = ctx.get("redis")
    queue: JobQueue = ArqJobQueue(redis) if redis is not None else NoopJobQueue()
    return MediaService(
        media=MediaRepository(session),
        posts=PostRepository(session),
        session=session,
        settings=get_settings(),
        imagekit=get_imagekit_client(),
        queue=queue,
    )


async def delete_remote_media(
    ctx: dict[str, Any],
    media_id: str,
    request_id: str | None = None,
) -> None:
    """Delete one Media file from ImageKit, retrying transient failures."""
    token = set_request_id(request_id or get_request_id())
    logger.info(
        "media deletion job started",
        extra={"job": "delete_remote_media", "media_id": media_id},
    )
    try:
        async with get_session_factory()() as session:
            service = _build_media_service(session, ctx)
            try:
                await service.delete_remote_media(UUID(media_id))
            except ImageKitClientError as exc:
                delay = _retry_delay(ctx, DELETE_REMOTE_MEDIA_MAX_TRIES)
                if delay is None:
                    logger.exception(
                        "media deletion exhausted retries",
                        extra={"job": "delete_remote_media", "media_id": media_id},
                    )
                    raise
                raise Retry(defer=delay) from exc
        logger.info(
            "media deletion job finished",
            extra={"job": "delete_remote_media", "media_id": media_id},
        )
    finally:
        reset_request_id(token)


async def verify_media(
    ctx: dict[str, Any],
    media_id: str,
    request_id: str | None = None,
) -> None:
    """Verify one Media file through ImageKit, retrying transient failures."""
    token = set_request_id(request_id or get_request_id())
    logger.info(
        "media verification job started",
        extra={"job": "verify_media", "media_id": media_id},
    )
    try:
        async with get_session_factory()() as session:
            service = _build_media_service(session, ctx)
            try:
                await service.verify_media(UUID(media_id))
            except ImageKitClientError as exc:
                delay = _retry_delay(ctx, VERIFY_MEDIA_MAX_TRIES)
                if delay is None:
                    logger.exception(
                        "media verification exhausted retries",
                        extra={"job": "verify_media", "media_id": media_id},
                    )
                    raise
                raise Retry(defer=delay) from exc
        logger.info(
            "media verification job finished",
            extra={"job": "verify_media", "media_id": media_id},
        )
    finally:
        reset_request_id(token)


async def sweep_orphans(
    ctx: dict[str, Any],
    request_id: str | None = None,
) -> None:
    """Re-enqueue pending deletions and retire old unreferenced pending media."""
    token = set_request_id(request_id or get_request_id())
    logger.info("orphan sweep job started", extra={"job": "sweep_orphans"})
    try:
        async with get_session_factory()() as session:
            service = _build_media_service(session, ctx)
            await service.sweep_orphans(request_id=request_id)
        logger.info("orphan sweep job finished", extra={"job": "sweep_orphans"})
    finally:
        reset_request_id(token)
