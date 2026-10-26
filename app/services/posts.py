"""Post lifecycle, ownership, slug, and cursor rules."""

import base64
import binascii
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    InsufficientRole,
    InvalidCursor,
    InvalidPostState,
    PostForbidden,
    PostNotFound,
)
from app.models.post import Post, PostStatus
from app.models.user import Role
from app.repositories.posts import PostRepository
from app.services.slugs import slugify
from app.services.tags import TagService


@dataclass(frozen=True)
class PostPage:
    """One page of posts and the cursor for the next page."""

    items: list[Post]
    next_cursor: str | None


class PostService:
    """Application rules for post reads and writes."""

    def __init__(
        self,
        *,
        posts: PostRepository,
        tags: TagService,
        session: AsyncSession,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._posts = posts
        self._tags = tags
        self._session = session
        self._clock = clock or (lambda: datetime.now(UTC))

    async def create(
        self,
        *,
        actor_id: UUID,
        actor_role: Role,
        title: str,
        body_md: str,
        tag_names: Sequence[str],
    ) -> Post:
        """Create a draft owned by the writer."""
        self._require_writer(actor_role)
        tags = await self._tags.find_or_create(tag_names)
        slug = await self._unique_slug(title)
        post = await self._posts.create(
            author_id=actor_id,
            slug=slug,
            title=title,
            body_md=body_md,
            tags=tags,
        )
        await self._session.commit()
        return post

    async def update(
        self,
        *,
        actor_id: UUID,
        actor_role: Role,
        post_id: UUID,
        title: str | None,
        body_md: str | None,
        tag_names: Sequence[str] | None,
    ) -> Post:
        """Edit a post without changing its slug."""
        self._require_writer(actor_role)
        post = await self._get_for_write(post_id, actor_id=actor_id, actor_role=actor_role)
        tags = await self._tags.find_or_create(tag_names) if tag_names is not None else None
        await self._posts.update_content(post, title=title, body_md=body_md)
        if tags is not None:
            await self._posts.replace_tags(post, tags)
        await self._session.commit()
        return post

    async def publish(
        self,
        *,
        actor_id: UUID,
        actor_role: Role,
        post_id: UUID,
    ) -> Post:
        """Publish a post, preserving the first publication timestamp."""
        self._require_writer(actor_role)
        post = await self._get_for_write(post_id, actor_id=actor_id, actor_role=actor_role)
        if post.status is PostStatus.published:
            return post
        published_at = post.published_at or self._clock()
        await self._posts.set_status(
            post,
            status=PostStatus.published,
            published_at=published_at,
        )
        await self._session.commit()
        return post

    async def archive(
        self,
        *,
        actor_id: UUID,
        actor_role: Role,
        post_id: UUID,
    ) -> Post:
        """Archive a published post; drafts cannot skip the published state."""
        self._require_writer(actor_role)
        post = await self._get_for_write(post_id, actor_id=actor_id, actor_role=actor_role)
        if post.status is PostStatus.archived:
            return post
        if post.status is PostStatus.draft:
            raise InvalidPostState("Only published posts can be archived")
        await self._posts.set_status(
            post,
            status=PostStatus.archived,
            published_at=post.published_at,
        )
        await self._session.commit()
        return post

    async def delete(
        self,
        *,
        actor_id: UUID,
        actor_role: Role,
        post_id: UUID,
    ) -> None:
        """Delete a post; association rows cascade and tags remain."""
        self._require_writer(actor_role)
        post = await self._get_for_write(post_id, actor_id=actor_id, actor_role=actor_role)
        await self._posts.delete(post)
        await self._session.commit()

    async def get_by_slug(
        self,
        slug: str,
        *,
        viewer_id: UUID | None,
        viewer_role: Role | None,
    ) -> Post:
        """Return a published post, or an unpublished one to its owner/admin."""
        post = await self._posts.get_by_slug(slug)
        if post is None:
            raise PostNotFound()
        if post.status is PostStatus.published:
            return post
        if viewer_id is not None and (post.author_id == viewer_id or viewer_role is Role.admin):
            return post
        raise PostNotFound()

    async def list_public(
        self,
        *,
        cursor: str | None,
        limit: int,
        tag_slug: str | None,
        author_id: UUID | None,
    ) -> PostPage:
        """List published posts newest first with optional tag/author filters."""
        after = _decode_cursor(cursor) if cursor is not None else None
        posts = await self._posts.list_published(
            after=after,
            limit=limit + 1,
            tag_slug=tag_slug,
            author_id=author_id,
        )
        items = posts[:limit]
        next_cursor = None
        if len(posts) > limit and items:
            last = items[-1]
            if last.published_at is not None:
                next_cursor = _encode_cursor(last.published_at, last.id)
        return PostPage(items=items, next_cursor=next_cursor)

    async def list_mine(
        self,
        *,
        actor_id: UUID,
        actor_role: Role,
        cursor: str | None,
        limit: int,
    ) -> PostPage:
        """List every post owned by the writer, in any status."""
        self._require_writer(actor_role)
        after = _decode_cursor(cursor) if cursor is not None else None
        posts = await self._posts.list_by_author(
            author_id=actor_id,
            after=after,
            limit=limit + 1,
        )
        items = posts[:limit]
        next_cursor = (
            _encode_cursor(items[-1].created_at, items[-1].id)
            if len(posts) > limit and items
            else None
        )
        return PostPage(items=items, next_cursor=next_cursor)

    async def _get_for_write(
        self,
        post_id: UUID,
        *,
        actor_id: UUID,
        actor_role: Role,
    ) -> Post:
        """Apply the non-owner rule: 404 for hidden posts, 403 for visible ones."""
        post = await self._posts.get_by_id(post_id, for_update=True)
        if post is None:
            raise PostNotFound()
        if actor_role is Role.admin or post.author_id == actor_id:
            return post
        if post.status is PostStatus.published:
            raise PostForbidden()
        raise PostNotFound()

    async def _unique_slug(self, title: str) -> str:
        base = slugify(title) or "post"
        slug = base
        suffix = 2
        while await self._posts.slug_exists(slug):
            slug = f"{base}-{suffix}"
            suffix += 1
        return slug

    def _require_writer(self, actor_role: Role) -> None:
        if actor_role not in {Role.author, Role.admin}:
            raise InsufficientRole()


def _encode_cursor(timestamp: datetime, entity_id: UUID) -> str:
    payload = {"ts": timestamp.isoformat(), "id": str(entity_id)}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = json.loads(decoded)
        if not isinstance(data, dict):
            raise InvalidCursor()
        timestamp_value = data["ts"]
        entity_id_value = data["id"]
        if not isinstance(timestamp_value, str) or not isinstance(entity_id_value, str):
            raise InvalidCursor()
        timestamp = datetime.fromisoformat(timestamp_value)
        if timestamp.tzinfo is None:
            raise InvalidCursor()
        entity_id = UUID(entity_id_value)
    except (binascii.Error, KeyError, TypeError, ValueError) as exc:
        raise InvalidCursor() from exc
    return timestamp.astimezone(UTC), entity_id
