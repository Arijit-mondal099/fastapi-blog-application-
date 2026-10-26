"""Public tag listing with published-post counts."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.repositories.tags import TagRepository
from app.schemas.tags import TagCountResponse
from app.services.tags import TagService

router = APIRouter(tags=["tags"])


def get_tag_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> TagService:
    """Build the tag service for one request."""
    return TagService(tags=TagRepository(session))


@router.get("/tags", response_model=list[TagCountResponse])
async def list_tags(
    service: Annotated[TagService, Depends(get_tag_service)],
) -> list[TagCountResponse]:
    """Return every tag and its number of published posts."""
    tags = await service.list_tags()
    return [
        TagCountResponse(
            id=tag.id,
            name=tag.name,
            slug=tag.slug,
            post_count=tag.post_count,
        )
        for tag in tags
    ]
