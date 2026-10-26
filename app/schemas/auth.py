"""Public response schemas for authentication."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.user import Role


class UserResponse(BaseModel):
    """Safe public representation of the current user."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    name: str | None
    avatar_url: str | None
    role: Role
    created_at: datetime
