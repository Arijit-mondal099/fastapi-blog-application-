"""Comment validation, visibility, ownership, and cursor rules."""

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    CommentForbidden,
    CommentNotFound,
    InvalidCommentBody,
    InvalidCursor,
    PostNotFound,
)
from app.models.comment import Comment
from app.models.post import Post, PostStatus
from app.models.user import Role
from app.repositories.comments import CommentRepository
from app.repositories.posts import PostRepository
from app.services.posts import is_post_visible

MAX_COMMENT_LENGTH = 2000


@dataclass(frozen=True)
class CommentPage:
    """One page of comments and the cursor for the next page."""

    items: list[Comment]
    next_cursor: str | None


class CommentService:
    """Application rules for flat comments."""

    def __init__(
        self,
        *,
        comments: CommentRepository,
        posts: PostRepository,
        session: AsyncSession,
    ) -> None:
        self._comments = comments
        self._posts = posts
        self._session = session

    async def create(self, *, slug: str, actor_id: UUID, body: str) -> Comment:
        """Create a comment on a published post only."""
        normalized_body = body.strip()
        if not 1 <= len(normalized_body) <= MAX_COMMENT_LENGTH:
            raise InvalidCommentBody()

        post = await self._posts.get_by_slug(slug)
        if post is None or post.status is not PostStatus.published:
            raise PostNotFound()

        comment = await self._comments.create(
            post_id=post.id,
            author_id=actor_id,
            body=normalized_body,
        )
        await self._session.commit()
        return comment

    async def list(
        self,
        *,
        slug: str,
        viewer_id: UUID | None,
        viewer_role: Role | None,
        cursor: str | None,
        limit: int,
    ) -> CommentPage:
        """List comments oldest first for a post the viewer may see."""
        post = await self._get_visible_post(
            slug,
            viewer_id=viewer_id,
            viewer_role=viewer_role,
        )
        after = _decode_comment_cursor(cursor) if cursor is not None else None
        comments = await self._comments.list_for_post(
            post_id=post.id,
            after=after,
            limit=limit + 1,
        )
        items = comments[:limit]
        next_cursor = None
        if len(comments) > limit and items:
            next_cursor = _encode_comment_cursor(items[-1])
        return CommentPage(items=items, next_cursor=next_cursor)

    async def delete(
        self,
        *,
        comment_id: UUID,
        actor_id: UUID,
        actor_role: Role,
    ) -> None:
        """Hard-delete a comment for its author, the post author, or an admin."""
        comment = await self._comments.get_by_id(comment_id, for_update=True)
        if comment is None:
            raise CommentNotFound()
        post = await self._posts.get_by_id(comment.post_id)
        if post is None:
            raise CommentNotFound()
        if (
            actor_id != comment.author_id
            and actor_id != post.author_id
            and actor_role is not Role.admin
        ):
            raise CommentForbidden()

        await self._comments.delete(comment)
        await self._session.commit()

    async def _get_visible_post(
        self,
        slug: str,
        *,
        viewer_id: UUID | None,
        viewer_role: Role | None,
    ) -> Post:
        post = await self._posts.get_by_slug(slug)
        if post is None or not is_post_visible(
            post,
            viewer_id=viewer_id,
            viewer_role=viewer_role,
        ):
            raise PostNotFound()
        return post


def _encode_comment_cursor(comment: Comment) -> str:
    payload = {"ts": comment.created_at.isoformat(), "id": str(comment.id)}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_comment_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = json.loads(decoded)
        if not isinstance(data, dict):
            raise InvalidCursor()
        timestamp_value = data["ts"]
        comment_id_value = data["id"]
        if not isinstance(timestamp_value, str) or not isinstance(comment_id_value, str):
            raise InvalidCursor()
        created_at = datetime.fromisoformat(timestamp_value)
        if created_at.tzinfo is None:
            raise InvalidCursor()
        comment_id = UUID(comment_id_value)
    except (binascii.Error, KeyError, TypeError, ValueError) as exc:
        raise InvalidCursor() from exc
    return created_at.astimezone(UTC), comment_id
