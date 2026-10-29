"""Media upload authentication, confirmation, verification, and deletion rules."""

import logging
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import (
    ImageKitNotConfigured,
    InsufficientRole,
    InvalidMediaMetadata,
    MediaAlreadyExists,
    MediaNotFound,
    PostForbidden,
    PostNotFound,
)
from app.core.logging import (
    get_request_id,
    reset_request_id,
    set_request_id,
)
from app.integrations.imagekit import (
    ImageKitClient,
    ImageKitFile,
    get_imagekit_client,
    sign_upload_auth,
)
from app.jobs.queue import JobQueue, NoopJobQueue
from app.models.media import Media, MediaStatus
from app.models.post import Post, PostStatus
from app.models.user import Role
from app.repositories.media import MediaRepository
from app.repositories.posts import PostRepository

DEFAULT_UPLOAD_AUTH_TTL_SECONDS = 300
MAX_UPLOAD_AUTH_TTL_SECONDS = 3600
MIN_UPLOAD_AUTH_TTL_SECONDS = 1
UPLOAD_FOLDER_PREFIX = "/media"
ORPHAN_GRACE_PERIOD = timedelta(hours=24)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UploadAuth:
    """Short-lived credentials for a direct browser-to-ImageKit upload."""

    token: str
    expire: int
    signature: str
    public_key: str
    folder: str
    file_name: str


class MediaService:
    """Application rules for media, post covers, and remote deletion."""

    def __init__(
        self,
        *,
        media: MediaRepository,
        posts: PostRepository,
        session: AsyncSession,
        settings: Settings,
        clock: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] | None = None,
        imagekit: ImageKitClient | None = None,
        queue: JobQueue | None = None,
    ) -> None:
        self._media = media
        self._posts = posts
        self._session = session
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))
        self._token_factory = token_factory or (lambda: secrets.token_hex(16))
        self._imagekit = imagekit or get_imagekit_client()
        self._queue = queue or NoopJobQueue()

    async def issue_upload_auth(
        self,
        *,
        actor_id: UUID,
        actor_role: Role,
        expires_in: int | None = None,
    ) -> UploadAuth:
        """Issue signed upload parameters to an author or admin."""
        self._require_writer(actor_role)
        private_key = self._settings.imagekit_private_key.get_secret_value()
        public_key = self._settings.imagekit_public_key
        if not private_key or not public_key:
            raise ImageKitNotConfigured()

        ttl = _clamp_ttl(expires_in)
        token = self._token_factory()
        expire = int(self._clock().timestamp()) + ttl
        signature = sign_upload_auth(
            private_key=private_key,
            token=token,
            expire=expire,
        )
        return UploadAuth(
            token=token,
            expire=expire,
            signature=signature,
            public_key=public_key,
            folder=f"{UPLOAD_FOLDER_PREFIX}/{actor_id}",
            file_name=token,
        )

    async def confirm_media(
        self,
        *,
        actor_id: UUID,
        actor_role: Role,
        imagekit_file_id: str,
        url: str,
        mime_type: str,
        size_bytes: int,
        request_id: str | None = None,
    ) -> Media:
        """Record a completed ImageKit upload as pending verification."""
        self._require_writer(actor_role)
        normalized_file_id = imagekit_file_id.strip()
        normalized_url = url.strip()
        normalized_mime_type = mime_type.strip()
        if (
            not normalized_file_id
            or not normalized_url
            or not normalized_mime_type
            or size_bytes <= 0
        ):
            raise InvalidMediaMetadata()

        if await self._media.get_by_imagekit_file_id(normalized_file_id) is not None:
            raise MediaAlreadyExists()

        try:
            media = await self._media.create(
                owner_id=actor_id,
                imagekit_file_id=normalized_file_id,
                url=normalized_url,
                mime_type=normalized_mime_type,
                size_bytes=size_bytes,
            )
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise MediaAlreadyExists() from exc

        await self._enqueue_verification(media.id, request_id=request_id)
        return media

    async def attach_cover(
        self,
        *,
        actor_id: UUID,
        actor_role: Role,
        post_id: UUID,
        media_id: UUID | None,
        commit: bool = True,
    ) -> Post:
        """Attach, replace, or clear a post cover after ownership checks."""
        self._require_writer(actor_role)
        post = await self._get_post_for_write(
            post_id,
            actor_id=actor_id,
            actor_role=actor_role,
        )
        if media_id is not None:
            media = await self._media.get_by_id(media_id, for_update=True)
            if media is None or (actor_role is not Role.admin and media.owner_id != actor_id):
                raise MediaNotFound()

        await self._posts.set_cover(post, media_id)
        if commit:
            await self._session.commit()
        return post

    async def mark_for_deletion(
        self,
        media_ids: Sequence[UUID],
        *,
        now: datetime | None = None,
    ) -> None:
        """Set media rows to the outbox state without committing the transaction."""
        await self._media.mark_pending_delete(media_ids, now=now or self._clock())

    async def enqueue_deletions(
        self,
        media_ids: Sequence[UUID],
        *,
        request_id: str | None = None,
    ) -> None:
        """Enqueue remote deletion after the transaction has committed."""
        request_id = request_id if request_id is not None else get_request_id()
        token = set_request_id(request_id)
        try:
            for media_id in media_ids:
                try:
                    await self._queue.enqueue(
                        "delete_remote_media",
                        str(media_id),
                        request_id=request_id,
                    )
                except Exception:
                    logger.exception(
                        "failed to enqueue media deletion; the sweep will recover it",
                        extra={"media_id": str(media_id)},
                    )
        finally:
            reset_request_id(token)

    async def delete_remote_media(self, media_id: UUID) -> None:
        """Delete one pending Media row from ImageKit and mark it deleted."""
        media = await self._media.get_by_id(media_id, for_update=True)
        if media is None:
            logger.info(
                "media deletion skipped: row no longer exists",
                extra={"media_id": str(media_id)},
            )
            return
        if media.status is MediaStatus.deleted:
            logger.info(
                "media deletion skipped: already deleted",
                extra={"media_id": str(media_id)},
            )
            return
        if media.status is not MediaStatus.pending_delete:
            logger.warning(
                "media deletion skipped: row is not pending_delete",
                extra={"media_id": str(media_id), "status": media.status.value},
            )
            return

        await self._imagekit.delete_file(media.imagekit_file_id)
        media.status = MediaStatus.deleted
        await self._session.commit()
        logger.info(
            "remote media deleted",
            extra={"media_id": str(media_id), "imagekit_file_id": media.imagekit_file_id},
        )

    async def verify_media(self, media_id: UUID) -> None:
        """Check ImageKit metadata and move Media to verified or pending deletion."""
        media = await self._media.get_by_id(media_id, for_update=True)
        if media is None:
            logger.info(
                "media verification skipped: row no longer exists",
                extra={"media_id": str(media_id)},
            )
            return
        if media.status is not MediaStatus.pending:
            logger.info(
                "media verification skipped: row is not pending",
                extra={"media_id": str(media_id), "status": media.status.value},
            )
            return

        details = await self._imagekit.get_file_details(media.imagekit_file_id)
        if details is None:
            media.status = MediaStatus.deleted
            await self._session.commit()
            logger.warning(
                "media verification found no remote file; marked deleted",
                extra={"media_id": str(media_id), "imagekit_file_id": media.imagekit_file_id},
            )
            return

        rejection_reason = _rejection_reason(details, self._settings)
        if rejection_reason is not None:
            media.status = MediaStatus.pending_delete
            await self._session.commit()
            logger.warning(
                "media verification failed; scheduled for deletion",
                extra={
                    "media_id": str(media_id),
                    "imagekit_file_id": media.imagekit_file_id,
                    "reason": rejection_reason,
                    "mime_type": details.mime_type,
                    "size_bytes": details.size_bytes,
                },
            )
            await self.enqueue_deletions([media.id])
            return

        media.status = MediaStatus.verified
        await self._session.commit()
        logger.info(
            "media verified",
            extra={"media_id": str(media_id), "imagekit_file_id": media.imagekit_file_id},
        )

    async def sweep_orphans(
        self,
        *,
        now: datetime | None = None,
        request_id: str | None = None,
    ) -> None:
        """Re-enqueue deletions and retire unreferenced pending media."""
        current = now or self._clock()
        cutoff = current - ORPHAN_GRACE_PERIOD
        stale_pending = await self._media.list_orphan_pending_ids(created_before=cutoff)
        if stale_pending:
            await self._media.mark_pending_delete(stale_pending, now=current)
        pending_delete = await self._media.list_pending_delete_ids()
        await self._session.commit()

        await self.enqueue_deletions(pending_delete, request_id=request_id)
        logger.info(
            "media orphan sweep completed",
            extra={
                "stale_pending": len(stale_pending),
                "pending_delete": len(pending_delete),
            },
        )

    async def _enqueue_verification(
        self,
        media_id: UUID,
        *,
        request_id: str | None,
    ) -> None:
        request_id = request_id if request_id is not None else get_request_id()
        token = set_request_id(request_id)
        try:
            await self._queue.enqueue(
                "verify_media",
                str(media_id),
                request_id=request_id,
            )
        except Exception:
            logger.exception(
                "failed to enqueue media verification",
                extra={"media_id": str(media_id)},
            )
        finally:
            reset_request_id(token)

    async def _get_post_for_write(
        self,
        post_id: UUID,
        *,
        actor_id: UUID,
        actor_role: Role,
    ) -> Post:
        post = await self._posts.get_by_id(post_id, for_update=True)
        if post is None:
            raise PostNotFound()
        if actor_role is Role.admin or post.author_id == actor_id:
            return post
        if post.status is PostStatus.published:
            raise PostForbidden()
        raise PostNotFound()

    def _require_writer(self, actor_role: Role) -> None:
        if actor_role not in {Role.author, Role.admin}:
            raise InsufficientRole()


def _rejection_reason(file: ImageKitFile, settings: Settings) -> str | None:
    mime_type = (file.mime_type or "").strip().lower()
    allowed = {mime.strip().lower() for mime in settings.media_allowed_mime_types}
    if mime_type not in allowed:
        return f"unsupported mime type: {file.mime_type!r}"
    if file.size_bytes is None or file.size_bytes <= 0:
        return "missing or invalid file size"
    if file.size_bytes > settings.media_max_size_bytes:
        return f"file size {file.size_bytes} exceeds limit {settings.media_max_size_bytes}"
    return None


def _clamp_ttl(expires_in: int | None) -> int:
    if expires_in is None:
        return DEFAULT_UPLOAD_AUTH_TTL_SECONDS
    return max(MIN_UPLOAD_AUTH_TTL_SECONDS, min(expires_in, MAX_UPLOAD_AUTH_TTL_SECONDS))
