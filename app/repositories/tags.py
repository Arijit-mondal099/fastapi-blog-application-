"""Database queries for tags."""

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.post import Post, PostStatus, PostTag, Tag


class TagRepository:
    """Persistence operations for tags."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_name(self, name: str) -> Tag | None:
        tag = await self._session.scalar(select(Tag).where(Tag.name == name))
        return tag if isinstance(tag, Tag) else None

    async def slug_exists(self, slug: str) -> bool:
        tag_id = await self._session.scalar(select(Tag.id).where(Tag.slug == slug).limit(1))
        return tag_id is not None

    async def create(self, *, name: str, slug: str) -> Tag:
        tag = Tag(name=name, slug=slug)
        self._session.add(tag)
        await self._session.flush()
        return tag

    async def list_with_published_counts(self) -> list[tuple[Tag, int]]:
        statement = (
            select(Tag, func.count(Post.id))
            .outerjoin(PostTag, PostTag.tag_id == Tag.id)
            .outerjoin(
                Post,
                and_(
                    Post.id == PostTag.post_id,
                    Post.status == PostStatus.published,
                ),
            )
            .group_by(Tag.id)
            .order_by(Tag.name)
        )
        rows = await self._session.execute(statement)
        return [(tag, int(count)) for tag, count in rows.all()]
