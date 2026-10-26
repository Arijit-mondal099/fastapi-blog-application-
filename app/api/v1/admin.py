"""Admin-only user management routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_role
from app.db.session import get_db_session
from app.models.user import Role, User
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.repositories.users import UserRepository
from app.schemas.admin import AdminUserListResponse, AdminUserResponse, RoleUpdate
from app.services.admin import AdminService

router = APIRouter(prefix="/admin", tags=["admin"])


def get_admin_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AdminService:
    """Build the admin service for one request."""
    return AdminService(
        users=UserRepository(session),
        refresh_tokens=RefreshTokenRepository(session),
        session=session,
    )


@router.patch("/users/{user_id}/role", response_model=AdminUserResponse)
async def update_user_role(
    user_id: UUID,
    body: RoleUpdate,
    actor: Annotated[CurrentUser, Depends(require_role(Role.admin))],
    service: Annotated[AdminService, Depends(get_admin_service)],
) -> User:
    """Change one user's role and revoke their existing sessions."""
    return await service.change_role(
        actor_id=actor.id,
        target_id=user_id,
        new_role=body.role,
    )


@router.get("/users", response_model=AdminUserListResponse)
async def list_users(
    _actor: Annotated[CurrentUser, Depends(require_role(Role.admin))],
    service: Annotated[AdminService, Depends(get_admin_service)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> AdminUserListResponse:
    """Return one cursor-paginated page of users."""
    page = await service.list_users(cursor=cursor, limit=limit)
    return AdminUserListResponse(
        items=[AdminUserResponse.model_validate(user) for user in page.items],
        next_cursor=page.next_cursor,
    )
