"""Database queries for comments."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.comment import Comment


class CommentRepository:
    """Persistence operations for comments."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, post_id: UUID, author_id: UUID, body: str) -> Comment:
        comment = Comment(post_id=post_id, author_id=author_id, body=body)
        self._session.add(comment)
        await self._session.flush()
        return comment

    async def get_by_id(self, comment_id: UUID, *, for_update: bool = False) -> Comment | None:
        statement = select(Comment).where(Comment.id == comment_id)
        if for_update:
            statement = statement.with_for_update()
        comment = await self._session.scalar(statement)
        return comment if isinstance(comment, Comment) else None

    async def list_for_post(
        self,
        *,
        post_id: UUID,
        after: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[Comment]:
        statement = select(Comment).where(Comment.post_id == post_id)
        if after is not None:
            created_at, comment_id = after
            statement = statement.where(
                or_(
                    Comment.created_at > created_at,
                    and_(
                        Comment.created_at == created_at,
                        Comment.id > comment_id,
                    ),
                )
            )
        statement = statement.order_by(Comment.created_at.asc(), Comment.id.asc()).limit(limit)
        comments = await self._session.scalars(statement)
        return list(comments.all())

    async def delete(self, comment: Comment) -> None:
        await self._session.delete(comment)
        await self._session.flush()
