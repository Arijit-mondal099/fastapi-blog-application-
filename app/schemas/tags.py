"""Request and response schemas for tags."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TagResponse(BaseModel):
    """Public tag fields."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str


class TagCountResponse(TagResponse):
    """A public tag with its number of published posts."""

    post_count: int
