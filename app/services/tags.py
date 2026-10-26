"""Tag validation, find-or-create, and published-post counts."""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from app.core.errors import InvalidTagName
from app.models.post import Tag
from app.repositories.tags import TagRepository
from app.services.slugs import slugify

MAX_TAG_NAME_LENGTH = 30


@dataclass(frozen=True)
class TagCount:
    """A tag and the number of published posts carrying it."""

    id: UUID
    name: str
    slug: str
    post_count: int


class TagService:
    """Application rules for tags."""

    def __init__(self, *, tags: TagRepository) -> None:
        self._tags = tags

    async def find_or_create(self, names: Sequence[str]) -> list[Tag]:
        """Normalize names, deduplicate them, and return their rows."""
        result: list[Tag] = []
        for name in _normalize_names(names):
            tag = await self._tags.get_by_name(name)
            if tag is None:
                slug = await self._unique_slug(name)
                tag = await self._tags.create(name=name, slug=slug)
            result.append(tag)
        return result

    async def list_tags(self) -> list[TagCount]:
        """Return every tag with a count limited to published posts."""
        rows = await self._tags.list_with_published_counts()
        return [
            TagCount(id=tag.id, name=tag.name, slug=tag.slug, post_count=count)
            for tag, count in rows
        ]

    async def _unique_slug(self, name: str) -> str:
        base = slugify(name) or "tag"
        slug = base
        suffix = 2
        while await self._tags.slug_exists(slug):
            slug = f"{base}-{suffix}"
            suffix += 1
        return slug


def _normalize_names(names: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw_name in names:
        name = raw_name.strip()
        if not name or len(name) > MAX_TAG_NAME_LENGTH:
            raise InvalidTagName()
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(name)
    return result
