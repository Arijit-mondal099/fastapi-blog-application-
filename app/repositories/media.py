"""Database queries for media records."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.media import Media, MediaStatus
from app.models.post import Post


class MediaRepository:
    """Persistence operations for media."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        owner_id: UUID,
        imagekit_file_id: str,
        url: str,
        mime_type: str,
        size_bytes: int,
    ) -> Media:
        media = Media(
            owner_id=owner_id,
            imagekit_file_id=imagekit_file_id,
            url=url,
            mime_type=mime_type,
            size_bytes=size_bytes,
            status=MediaStatus.pending,
        )
        self._session.add(media)
        await self._session.flush()
        return media

    async def get_by_id(self, media_id: UUID, *, for_update: bool = False) -> Media | None:
        statement = select(Media).where(Media.id == media_id)
        if for_update:
            statement = statement.with_for_update()
        media = await self._session.scalar(statement)
        return media if isinstance(media, Media) else None

    async def get_by_imagekit_file_id(self, imagekit_file_id: str) -> Media | None:
        media = await self._session.scalar(
            select(Media).where(Media.imagekit_file_id == imagekit_file_id)
        )
        return media if isinstance(media, Media) else None

    async def mark_pending_delete(
        self,
        media_ids: Sequence[UUID],
        *,
        now: datetime,
    ) -> None:
        """Mark media for remote deletion, leaving already-deleted rows alone."""
        if not media_ids:
            return
        await self._session.execute(
            update(Media)
            .where(
                Media.id.in_(media_ids),
                Media.status != MediaStatus.deleted,
            )
            .values(status=MediaStatus.pending_delete, updated_at=now)
        )
        await self._session.flush()

    async def list_pending_delete_ids(self) -> list[UUID]:
        """Return every media row waiting for remote deletion."""
        media_ids = await self._session.scalars(
            select(Media.id).where(Media.status == MediaStatus.pending_delete)
        )
        return list(media_ids.all())

    async def list_orphan_pending_ids(
        self,
        *,
        created_before: datetime,
    ) -> list[UUID]:
        """Return unreferenced pending media older than the grace period."""
        cover_exists = exists().where(Post.cover_media_id == Media.id)
        media_ids = await self._session.scalars(
            select(Media.id)
            .where(
                Media.status == MediaStatus.pending,
                Media.created_at < created_before,
                ~cover_exists,
            )
            .with_for_update(skip_locked=True)
        )
        return list(media_ids.all())
