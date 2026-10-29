"""Comment routes: public reads plus authenticated create and delete."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_optional_current_user, require_role
from app.db.session import get_db_session
from app.models.comment import Comment
from app.models.user import Role
from app.repositories.comments import CommentRepository
from app.repositories.posts import PostRepository
from app.schemas.comments import CommentCreate, CommentListResponse, CommentResponse
from app.services.comments import CommentPage, CommentService

router = APIRouter(tags=["comments"])


def get_comment_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CommentService:
    """Build the comment service for one request."""
    return CommentService(
        comments=CommentRepository(session),
        posts=PostRepository(session),
        session=session,
    )


def _page_response(page: CommentPage) -> CommentListResponse:
    return CommentListResponse(
        items=[CommentResponse.model_validate(comment) for comment in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/posts/{slug}/comments", response_model=CommentListResponse)
async def list_comments(
    slug: str,
    service: Annotated[CommentService, Depends(get_comment_service)],
    viewer: Annotated[CurrentUser | None, Depends(get_optional_current_user)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> CommentListResponse:
    """List a visible post's comments, oldest first."""
    page = await service.list(
        slug=slug,
        viewer_id=viewer.id if viewer is not None else None,
        viewer_role=viewer.role if viewer is not None else None,
        cursor=cursor,
        limit=limit,
    )
    return _page_response(page)


@router.post("/posts/{slug}/comments", response_model=CommentResponse, status_code=201)
async def create_comment(
    slug: str,
    body: CommentCreate,
    actor: Annotated[CurrentUser, Depends(require_role(Role.reader))],
    service: Annotated[CommentService, Depends(get_comment_service)],
) -> Comment:
    """Create a comment on a published post."""
    return await service.create(
        slug=slug,
        actor_id=actor.id,
        body=body.body,
    )


@router.delete("/comments/{comment_id}", status_code=204)
async def delete_comment(
    comment_id: UUID,
    actor: Annotated[CurrentUser, Depends(require_role(Role.reader))],
    service: Annotated[CommentService, Depends(get_comment_service)],
) -> Response:
    """Hard-delete a comment for its author, the post author, or an admin."""
    await service.delete(
        comment_id=comment_id,
        actor_id=actor.id,
        actor_role=actor.role,
    )
    return Response(status_code=204)
