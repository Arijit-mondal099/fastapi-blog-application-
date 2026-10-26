"""Response schemas for public search."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SearchItemResponse(BaseModel):
    """A ranked search result without the full post body."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    slug: str
    excerpt: str
    published_at: datetime
    rank: float


class SearchResponse(BaseModel):
    """One cursor-paginated page of search results."""

    items: list[SearchItemResponse]
    next_cursor: str | None
