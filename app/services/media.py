"""Media upload authentication, confirmation, and cover attachment rules."""

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
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
from app.integrations.imagekit import sign_upload_auth
from app.models.media import Media
from app.models.post import Post, PostStatus
from app.models.user import Role
from app.repositories.media import MediaRepository
from app.repositories.posts import PostRepository

DEFAULT_UPLOAD_AUTH_TTL_SECONDS = 300
MAX_UPLOAD_AUTH_TTL_SECONDS = 3600
MIN_UPLOAD_AUTH_TTL_SECONDS = 1
UPLOAD_FOLDER_PREFIX = "/media"


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
    """Application rules for media and post covers."""

    def __init__(
        self,
        *,
        media: MediaRepository,
        posts: PostRepository,
        session: AsyncSession,
        settings: Settings,
        clock: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self._media = media
        self._posts = posts
        self._session = session
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))
        self._token_factory = token_factory or (lambda: secrets.token_hex(16))

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


def _clamp_ttl(expires_in: int | None) -> int:
    if expires_in is None:
        return DEFAULT_UPLOAD_AUTH_TTL_SECONDS
    return max(MIN_UPLOAD_AUTH_TTL_SECONDS, min(expires_in, MAX_UPLOAD_AUTH_TTL_SECONDS))
