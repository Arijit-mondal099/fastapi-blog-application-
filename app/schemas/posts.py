"""Request and response schemas for posts."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.post import PostStatus
from app.schemas.tags import TagResponse


class PostCreate(BaseModel):
    """Fields accepted when creating a draft post."""

    title: str = Field(min_length=1)
    body_md: str = ""
    tags: list[str] = Field(default_factory=list)


class PostUpdate(BaseModel):
    """Fields that may be changed on an existing post."""

    title: str | None = Field(default=None, min_length=1)
    body_md: str | None = None
    tags: list[str] | None = None


class PostResponse(BaseModel):
    """Public and author-facing post fields."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    author_id: UUID
    slug: str
    title: str
    body_md: str
    status: PostStatus
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime
    tags: list[TagResponse]


class PostListResponse(BaseModel):
    """One cursor-paginated page of posts."""

    items: list[PostResponse]
    next_cursor: str | None
