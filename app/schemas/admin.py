"""Request and response schemas for admin user management."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.user import Role


class RoleUpdate(BaseModel):
    """A requested role for one user."""

    role: Role


class AdminUserResponse(BaseModel):
    """Admin-facing user fields without the Google identity."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    name: str | None
    role: Role


class AdminUserListResponse(BaseModel):
    """One cursor-paginated page of users."""

    items: list[AdminUserResponse]
    next_cursor: str | None
