"""Authentication dependencies shared by routes."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request

from app.core.config import get_settings
from app.core.cookies import ACCESS_COOKIE_NAME
from app.core.errors import AuthenticationError, InsufficientRole
from app.core.security import decode_access_token
from app.models.user import Role

ROLE_RANK: dict[Role, int] = {
    Role.reader: 0,
    Role.author: 1,
    Role.admin: 2,
}


@dataclass(frozen=True)
class CurrentUser:
    """Identity and session derived from a verified access token."""

    id: UUID
    role: Role
    family_id: UUID


async def get_current_user(request: Request) -> CurrentUser:
    """Read and verify the access cookie."""
    token = request.cookies.get(ACCESS_COOKIE_NAME)
    if not token:
        raise AuthenticationError()
    claims = decode_access_token(token, get_settings())
    return CurrentUser(id=claims.user_id, role=claims.role, family_id=claims.family_id)


async def get_optional_current_user(request: Request) -> CurrentUser | None:
    """Read the access cookie when present, otherwise stay anonymous."""
    token = request.cookies.get(ACCESS_COOKIE_NAME)
    if not token:
        return None
    claims = decode_access_token(token, get_settings())
    return CurrentUser(id=claims.user_id, role=claims.role, family_id=claims.family_id)


def require_role(
    min_role: Role,
) -> Callable[[Annotated[CurrentUser, Depends(get_current_user)]], Awaitable[CurrentUser]]:
    """Build a dependency that requires at least ``min_role``."""

    async def dependency(
        user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> CurrentUser:
        if ROLE_RANK[user.role] < ROLE_RANK[min_role]:
            raise InsufficientRole()
        return user

    return dependency
