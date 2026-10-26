"""Administrative user-management rules."""

import base64
import binascii
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    AuthenticationError,
    CannotDemoteSelf,
    InsufficientRole,
    InvalidCursor,
    UserNotFound,
)
from app.models.user import Role, User
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.repositories.users import UserRepository

AUDIT_LOGGER_NAME = "app.audit"


@dataclass(frozen=True)
class UserPage:
    """One page of users and the cursor for the next page."""

    items: list[User]
    next_cursor: str | None


class AdminService:
    """Application rules for admin-only user management."""

    def __init__(
        self,
        *,
        users: UserRepository,
        refresh_tokens: RefreshTokenRepository,
        session: AsyncSession,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._users = users
        self._refresh_tokens = refresh_tokens
        self._session = session
        self._clock = clock or (lambda: datetime.now(UTC))

    async def change_role(self, *, actor_id: UUID, target_id: UUID, new_role: Role) -> User:
        """Change a user's role, revoking their sessions when the role actually changes."""
        actor = await self._users.get_by_id(actor_id)
        if actor is None:
            raise AuthenticationError()
        if actor.role != Role.admin:
            raise InsufficientRole()

        target = await self._users.get_by_id(target_id)
        if target is None:
            raise UserNotFound()
        if target.id == actor.id and new_role != Role.admin:
            raise CannotDemoteSelf()
        if target.role == new_role:
            return target

        old_role = target.role
        await self._users.update_role(target, new_role)
        await self._refresh_tokens.revoke_all_for_user(
            target.id,
            revoked_at=self._clock(),
        )
        await self._session.commit()

        logging.getLogger(AUDIT_LOGGER_NAME).info(
            "role_changed",
            extra={
                "event": "role_changed",
                "actor_id": str(actor.id),
                "target_id": str(target.id),
                "old_role": old_role.value,
                "new_role": new_role.value,
            },
        )
        return target

    async def list_users(self, *, cursor: str | None, limit: int) -> UserPage:
        """Return a stable page ordered by creation time and id, newest first."""
        after = _decode_cursor(cursor) if cursor is not None else None
        users = await self._users.list_page(after=after, limit=limit + 1)
        items = users[:limit]
        next_cursor = _encode_cursor(items[-1]) if len(users) > limit and items else None
        return UserPage(items=items, next_cursor=next_cursor)


def _encode_cursor(user: User) -> str:
    payload = {"created_at": user.created_at.isoformat(), "id": str(user.id)}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = json.loads(decoded)
        if not isinstance(data, dict):
            raise InvalidCursor()
        created_at_value = data["created_at"]
        user_id_value = data["id"]
        if not isinstance(created_at_value, str) or not isinstance(user_id_value, str):
            raise InvalidCursor()
        created_at = datetime.fromisoformat(created_at_value)
        if created_at.tzinfo is None:
            raise InvalidCursor()
        user_id = UUID(user_id_value)
    except (binascii.Error, KeyError, TypeError, ValueError) as exc:
        raise InvalidCursor() from exc
    return created_at.astimezone(UTC), user_id
