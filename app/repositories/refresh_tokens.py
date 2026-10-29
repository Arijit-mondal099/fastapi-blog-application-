"""Database queries for refresh-token families."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.refresh_token import RefreshToken


class RefreshTokenRepository:
    """Persistence operations for refresh tokens."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, token: RefreshToken) -> RefreshToken:
        self._session.add(token)
        await self._session.flush()
        return token

    async def get_by_hash(
        self, token_hash: str, *, for_update: bool = False
    ) -> RefreshToken | None:
        statement = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        if for_update:
            statement = statement.with_for_update()
        token = await self._session.scalar(statement)
        return token if isinstance(token, RefreshToken) else None

    async def revoke_family(
        self,
        family_id: UUID,
        *,
        revoked_at: datetime,
        user_id: UUID | None = None,
    ) -> None:
        statement = update(RefreshToken).where(
            RefreshToken.family_id == family_id,
            RefreshToken.revoked_at.is_(None),
        )
        if user_id is not None:
            statement = statement.where(RefreshToken.user_id == user_id)
        await self._session.execute(statement.values(revoked_at=revoked_at))

    async def revoke_all_for_user(self, user_id: UUID, *, revoked_at: datetime) -> None:
        statement = update(RefreshToken).where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
        )
        await self._session.execute(statement.values(revoked_at=revoked_at))

    async def mark_rotated(
        self, token: RefreshToken, *, revoked_at: datetime, replaced_by: UUID
    ) -> None:
        token.revoked_at = revoked_at
        token.replaced_by = replaced_by
        await self._session.flush()

    async def purge_expired(
        self,
        *,
        now: datetime,
        revoked_before: datetime,
    ) -> int:
        """Delete expired rows and revoked rows older than the retention window."""
        result = await self._session.execute(
            delete(RefreshToken).where(
                or_(
                    RefreshToken.expires_at < now,
                    and_(
                        RefreshToken.revoked_at.is_not(None),
                        RefreshToken.revoked_at < revoked_before,
                    ),
                )
            )
        )
        return int(getattr(result, "rowcount", 0) or 0)
