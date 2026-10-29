"""Post routes: public reads plus author/admin management."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import CurrentUser, get_optional_current_user, require_role
from app.db.session import get_db_session
from app.jobs.queue import JobQueue, get_job_queue
from app.models.post import Post
from app.models.user import Role
from app.repositories.media import MediaRepository
from app.repositories.posts import PostRepository
from app.repositories.tags import TagRepository
from app.schemas.posts import PostCreate, PostListResponse, PostResponse, PostUpdate
from app.services.media import MediaService
from app.services.posts import PostService
from app.services.tags import TagService

router = APIRouter(tags=["posts"])


def get_post_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    queue: Annotated[JobQueue, Depends(get_job_queue)],
) -> PostService:
    """Build the post service for one request."""
    posts = PostRepository(session)
    return PostService(
        posts=posts,
        tags=TagService(tags=TagRepository(session)),
        media=MediaService(
            media=MediaRepository(session),
            posts=posts,
            session=session,
            settings=get_settings(),
            queue=queue,
        ),
        session=session,
    )


def _page_response(page_items: list[Post], next_cursor: str | None) -> PostListResponse:
    return PostListResponse(
        items=[PostResponse.model_validate(post) for post in page_items],
        next_cursor=next_cursor,
    )


@router.get("/posts", response_model=PostListResponse)
async def list_posts(
    service: Annotated[PostService, Depends(get_post_service)],
    tag: str | None = None,
    author: UUID | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> PostListResponse:
    """List published posts, newest first."""
    page = await service.list_public(
        cursor=cursor,
        limit=limit,
        tag_slug=tag,
        author_id=author,
    )
    return _page_response(page.items, page.next_cursor)


@router.get("/posts/{slug}", response_model=PostResponse)
async def get_post(
    slug: str,
    service: Annotated[PostService, Depends(get_post_service)],
    viewer: Annotated[CurrentUser | None, Depends(get_optional_current_user)],
) -> Post:
    """Return a published post, or an unpublished one to its owner/admin."""
    return await service.get_by_slug(
        slug,
        viewer_id=viewer.id if viewer is not None else None,
        viewer_role=viewer.role if viewer is not None else None,
    )


@router.post("/posts", response_model=PostResponse, status_code=201)
async def create_post(
    body: PostCreate,
    actor: Annotated[CurrentUser, Depends(require_role(Role.author))],
    service: Annotated[PostService, Depends(get_post_service)],
) -> Post:
    """Create a draft owned by the caller."""
    return await service.create(
        actor_id=actor.id,
        actor_role=actor.role,
        title=body.title,
        body_md=body.body_md,
        tag_names=body.tags,
    )


@router.patch("/posts/{post_id}", response_model=PostResponse)
async def update_post(
    post_id: UUID,
    body: PostUpdate,
    actor: Annotated[CurrentUser, Depends(require_role(Role.author))],
    service: Annotated[PostService, Depends(get_post_service)],
) -> Post:
    """Edit title, body, tags, or cover; the slug never changes."""
    return await service.update(
        actor_id=actor.id,
        actor_role=actor.role,
        post_id=post_id,
        title=body.title,
        body_md=body.body_md,
        tag_names=body.tags,
        cover_media_id=body.cover_media_id,
        cover_provided="cover_media_id" in body.model_fields_set,
    )


@router.post("/posts/{post_id}/publish", response_model=PostResponse)
async def publish_post(
    post_id: UUID,
    actor: Annotated[CurrentUser, Depends(require_role(Role.author))],
    service: Annotated[PostService, Depends(get_post_service)],
) -> Post:
    """Publish a draft or restore an archived post."""
    return await service.publish(
        actor_id=actor.id,
        actor_role=actor.role,
        post_id=post_id,
    )


@router.post("/posts/{post_id}/archive", response_model=PostResponse)
async def archive_post(
    post_id: UUID,
    actor: Annotated[CurrentUser, Depends(require_role(Role.author))],
    service: Annotated[PostService, Depends(get_post_service)],
) -> Post:
    """Archive a published post."""
    return await service.archive(
        actor_id=actor.id,
        actor_role=actor.role,
        post_id=post_id,
    )


@router.delete("/posts/{post_id}", status_code=204)
async def delete_post(
    post_id: UUID,
    request: Request,
    actor: Annotated[CurrentUser, Depends(require_role(Role.author))],
    service: Annotated[PostService, Depends(get_post_service)],
) -> Response:
    """Delete a post; its tag links cascade but tag rows remain."""
    await service.delete(
        actor_id=actor.id,
        actor_role=actor.role,
        post_id=post_id,
        request_id=getattr(request.state, "request_id", None),
    )
    return Response(status_code=204)


@router.get("/me/posts", response_model=PostListResponse)
async def list_my_posts(
    actor: Annotated[CurrentUser, Depends(require_role(Role.author))],
    service: Annotated[PostService, Depends(get_post_service)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> PostListResponse:
    """List every post owned by the caller, in any status."""
    page = await service.list_mine(
        actor_id=actor.id,
        actor_role=actor.role,
        cursor=cursor,
        limit=limit,
    )
    return _page_response(page.items, page.next_cursor)
