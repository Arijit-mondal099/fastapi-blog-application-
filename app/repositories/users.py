"""Database queries for users."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import Role, User


class UserRepository:
    """Persistence operations for users."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_google_sub(self, google_sub: str) -> User | None:
        user = await self._session.scalar(select(User).where(User.google_sub == google_sub))
        return user if isinstance(user, User) else None

    async def get_by_id(self, user_id: UUID) -> User | None:
        user = await self._session.get(User, user_id)
        return user if isinstance(user, User) else None

    async def create(
        self,
        *,
        google_sub: str,
        email: str,
        name: str | None,
        avatar_url: str | None,
        role: Role,
    ) -> User:
        user = User(
            google_sub=google_sub,
            email=email,
            name=name,
            avatar_url=avatar_url,
            role=role,
        )
        self._session.add(user)
        await self._session.flush()
        return user

    async def update_profile(
        self,
        user: User,
        *,
        email: str,
        name: str | None,
        avatar_url: str | None,
    ) -> None:
        user.email = email
        user.name = name
        user.avatar_url = avatar_url
        await self._session.flush()

    async def update_role(self, user: User, role: Role) -> None:
        user.role = role
        await self._session.flush()

    async def list_page(
        self,
        *,
        after: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[User]:
        statement = select(User)
        if after is not None:
            created_at, user_id = after
            statement = statement.where(
                or_(
                    User.created_at < created_at,
                    and_(User.created_at == created_at, User.id < user_id),
                )
            )
        statement = statement.order_by(User.created_at.desc(), User.id.desc()).limit(limit)
        users = await self._session.scalars(statement)
        return list(users.all())
