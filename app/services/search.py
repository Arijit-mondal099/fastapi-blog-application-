"""Search query validation, cursor handling, and public result shaping."""

import base64
import binascii
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.core.errors import InvalidCursor, InvalidSearchQuery
from app.models.post import Post
from app.repositories.posts import PostRepository

MAX_SEARCH_QUERY_LENGTH = 200
EXCERPT_LENGTH = 200


@dataclass(frozen=True)
class SearchItem:
    """One public search result without the post body."""

    id: UUID
    title: str
    slug: str
    excerpt: str
    published_at: datetime
    rank: float


@dataclass(frozen=True)
class SearchPage:
    """One page of search results and the cursor for the next page."""

    items: list[SearchItem]
    next_cursor: str | None


class SearchService:
    """Application rules for public full-text search."""

    def __init__(self, *, posts: PostRepository) -> None:
        self._posts = posts

    async def search(
        self,
        *,
        query: str,
        cursor: str | None,
        limit: int,
    ) -> SearchPage:
        """Search published posts by relevance and build the next cursor."""
        normalized_query = query.strip()
        if not normalized_query or len(normalized_query) > MAX_SEARCH_QUERY_LENGTH:
            raise InvalidSearchQuery()

        after = _decode_search_cursor(cursor) if cursor is not None else None
        rows = await self._posts.search(
            query=normalized_query,
            after=after,
            limit=limit + 1,
        )
        items = [_to_item(post, rank) for post, rank in rows[:limit]]
        next_cursor = None
        if len(rows) > limit and items:
            last = items[-1]
            next_cursor = _encode_search_cursor(last.rank, last.published_at, last.id)
        return SearchPage(items=items, next_cursor=next_cursor)


def _to_item(post: Post, rank: float) -> SearchItem:
    assert post.published_at is not None
    return SearchItem(
        id=post.id,
        title=post.title,
        slug=post.slug,
        excerpt=post.body_md[:EXCERPT_LENGTH],
        published_at=post.published_at,
        rank=rank,
    )


def _encode_search_cursor(rank: float, published_at: datetime, entity_id: UUID) -> str:
    payload = {
        "rank": rank,
        "published_at": published_at.isoformat(),
        "id": str(entity_id),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_search_cursor(cursor: str) -> tuple[float, datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(
            padded.encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
        if canonical != cursor:
            raise InvalidCursor()
        data = json.loads(decoded)
        if not isinstance(data, dict) or set(data) != {"rank", "published_at", "id"}:
            raise InvalidCursor()
        rank_value = data["rank"]
        published_at_value = data["published_at"]
        entity_id_value = data["id"]
        if (
            isinstance(rank_value, bool)
            or not isinstance(rank_value, (int, float))
            or not isinstance(published_at_value, str)
            or not isinstance(entity_id_value, str)
        ):
            raise InvalidCursor()
        rank = float(rank_value)
        if not math.isfinite(rank) or rank < 0:
            raise InvalidCursor()
        published_at = datetime.fromisoformat(published_at_value)
        if published_at.tzinfo is None:
            raise InvalidCursor()
        entity_id = UUID(entity_id_value)
    except (binascii.Error, KeyError, OverflowError, TypeError, ValueError) as exc:
        raise InvalidCursor() from exc
    return rank, published_at.astimezone(UTC), entity_id
