"""Request and response schemas for comments."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CommentCreate(BaseModel):
    """Fields accepted when creating a comment."""

    body: str


class CommentResponse(BaseModel):
    """A flat comment as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    post_id: UUID
    author_id: UUID
    body: str
    created_at: datetime
    updated_at: datetime


class CommentListResponse(BaseModel):
    """One cursor-paginated page of comments."""

    items: list[CommentResponse]
    next_cursor: str | None
