"""Database queries for media records."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.media import Media, MediaStatus


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
