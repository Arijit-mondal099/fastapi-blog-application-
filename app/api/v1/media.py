"""Media upload authentication and upload confirmation routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import CurrentUser, require_role
from app.db.session import get_db_session
from app.jobs.queue import JobQueue, get_job_queue
from app.models.media import Media
from app.models.user import Role
from app.repositories.media import MediaRepository
from app.repositories.posts import PostRepository
from app.schemas.media import MediaConfirmRequest, MediaResponse, UploadAuthResponse
from app.services.media import MediaService

router = APIRouter(tags=["media"])


def get_media_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    queue: Annotated[JobQueue, Depends(get_job_queue)],
) -> MediaService:
    """Build the media service for one request."""
    return MediaService(
        media=MediaRepository(session),
        posts=PostRepository(session),
        session=session,
        settings=get_settings(),
        queue=queue,
    )


@router.get("/media/upload-auth", response_model=UploadAuthResponse)
async def get_upload_auth(
    actor: Annotated[CurrentUser, Depends(require_role(Role.author))],
    service: Annotated[MediaService, Depends(get_media_service)],
    expires_in: int | None = None,
    expire: int | None = None,
) -> UploadAuthResponse:
    """Issue short-lived signed upload parameters to an author or admin."""
    requested_ttl = expires_in if expires_in is not None else expire
    auth = await service.issue_upload_auth(
        actor_id=actor.id,
        actor_role=actor.role,
        expires_in=requested_ttl,
    )
    return UploadAuthResponse(
        token=auth.token,
        expire=auth.expire,
        signature=auth.signature,
        public_key=auth.public_key,
        folder=auth.folder,
        file_name=auth.file_name,
    )


@router.post("/media", response_model=MediaResponse, status_code=201)
async def confirm_media(
    request: Request,
    body: MediaConfirmRequest,
    actor: Annotated[CurrentUser, Depends(require_role(Role.author))],
    service: Annotated[MediaService, Depends(get_media_service)],
) -> Media:
    """Record a completed ImageKit upload as pending verification."""
    return await service.confirm_media(
        actor_id=actor.id,
        actor_role=actor.role,
        imagekit_file_id=body.imagekit_file_id,
        url=body.url,
        mime_type=body.mime_type,
        size_bytes=body.size_bytes,
        request_id=getattr(request.state, "request_id", None),
    )
