"""Public full-text search route."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.repositories.posts import PostRepository
from app.schemas.search import SearchItemResponse, SearchResponse
from app.services.search import SearchPage, SearchService

router = APIRouter(tags=["search"])


def get_search_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SearchService:
    """Build the search service for one request."""
    return SearchService(posts=PostRepository(session))


def _response(page: SearchPage) -> SearchResponse:
    return SearchResponse(
        items=[SearchItemResponse.model_validate(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/search", response_model=SearchResponse)
async def search(
    service: Annotated[SearchService, Depends(get_search_service)],
    q: str,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> SearchResponse:
    """Search published posts by relevance."""
    page = await service.search(query=q, cursor=cursor, limit=limit)
    return _response(page)
