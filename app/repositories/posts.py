"""Database queries for posts."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.post import Post, PostStatus, PostTag, Tag


class PostRepository:
    """Persistence operations for posts."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        author_id: UUID,
        slug: str,
        title: str,
        body_md: str,
        tags: Sequence[Tag],
    ) -> Post:
        post = Post(
            author_id=author_id,
            slug=slug,
            title=title,
            body_md=body_md,
            status=PostStatus.draft,
            tags=list(tags),
        )
        self._session.add(post)
        await self._session.flush()
        return post

    async def get_by_id(self, post_id: UUID, *, for_update: bool = False) -> Post | None:
        statement = select(Post).where(Post.id == post_id)
        if for_update:
            statement = statement.with_for_update()
        post = await self._session.scalar(statement)
        return post if isinstance(post, Post) else None

    async def get_by_slug(self, slug: str) -> Post | None:
        post = await self._session.scalar(select(Post).where(Post.slug == slug))
        return post if isinstance(post, Post) else None

    async def slug_exists(self, slug: str) -> bool:
        post_id = await self._session.scalar(select(Post.id).where(Post.slug == slug).limit(1))
        return post_id is not None

    async def list_published(
        self,
        *,
        after: tuple[datetime, UUID] | None,
        limit: int,
        tag_slug: str | None,
        author_id: UUID | None,
    ) -> list[Post]:
        statement = select(Post).where(
            Post.status == PostStatus.published,
            Post.published_at.is_not(None),
        )
        if tag_slug is not None:
            statement = (
                statement.join(PostTag, PostTag.post_id == Post.id)
                .join(Tag, Tag.id == PostTag.tag_id)
                .where(Tag.slug == tag_slug)
            )
        if author_id is not None:
            statement = statement.where(Post.author_id == author_id)
        if after is not None:
            published_at, post_id = after
            statement = statement.where(
                or_(
                    Post.published_at < published_at,
                    and_(Post.published_at == published_at, Post.id < post_id),
                )
            )
        statement = statement.order_by(Post.published_at.desc(), Post.id.desc()).limit(limit)
        posts = await self._session.scalars(statement)
        return list(posts.all())

    async def list_by_author(
        self,
        *,
        author_id: UUID,
        after: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[Post]:
        statement = select(Post).where(Post.author_id == author_id)
        if after is not None:
            created_at, post_id = after
            statement = statement.where(
                or_(
                    Post.created_at < created_at,
                    and_(Post.created_at == created_at, Post.id < post_id),
                )
            )
        statement = statement.order_by(Post.created_at.desc(), Post.id.desc()).limit(limit)
        posts = await self._session.scalars(statement)
        return list(posts.all())

    async def search(
        self,
        *,
        query: str,
        after: tuple[float, datetime, UUID] | None,
        limit: int,
    ) -> list[tuple[Post, float]]:
        tsquery = func.websearch_to_tsquery("english", query)
        rank = func.ts_rank(Post.search_vector, tsquery)
        statement = select(Post, rank.label("rank")).where(
            Post.status == PostStatus.published,
            Post.published_at.is_not(None),
            Post.search_vector.op("@@")(tsquery),
        )
        if after is not None:
            rank_value, published_at, post_id = after
            statement = statement.where(
                or_(
                    rank < rank_value,
                    and_(rank == rank_value, Post.published_at < published_at),
                    and_(
                        rank == rank_value,
                        Post.published_at == published_at,
                        Post.id < post_id,
                    ),
                )
            )
        statement = statement.order_by(
            rank.desc(),
            Post.published_at.desc(),
            Post.id.desc(),
        ).limit(limit)
        rows = await self._session.execute(statement)
        return [(post, float(rank_value)) for post, rank_value in rows.all()]

    async def update_content(
        self,
        post: Post,
        *,
        title: str | None,
        body_md: str | None,
    ) -> None:
        if title is not None:
            post.title = title
        if body_md is not None:
            post.body_md = body_md
        await self._session.flush()
        await self._session.refresh(post, attribute_names=["updated_at"])

    async def replace_tags(self, post: Post, tags: Sequence[Tag]) -> None:
        post.tags = list(tags)
        await self._session.flush()

    async def set_cover(self, post: Post, cover_media_id: UUID | None) -> None:
        post.cover_media_id = cover_media_id
        await self._session.flush()
        await self._session.refresh(post, attribute_names=["updated_at"])

    async def set_status(
        self,
        post: Post,
        *,
        status: PostStatus,
        published_at: datetime | None,
    ) -> None:
        post.status = status
        post.published_at = published_at
        await self._session.flush()
        await self._session.refresh(post, attribute_names=["updated_at"])

    async def delete(self, post: Post) -> None:
        await self._session.delete(post)
        await self._session.flush()
