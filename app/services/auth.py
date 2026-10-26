"""Authentication business rules: login, rotation, reuse detection, logout."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import (
    AuthenticationError,
    EmailNotVerified,
    InvalidToken,
    RefreshTokenReuse,
)
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
    hash_token,
)
from app.integrations.google_oauth import GoogleIdentity
from app.models.refresh_token import RefreshToken
from app.models.user import Role, User
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.repositories.users import UserRepository


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str


class AuthService:
    """Application rules for user sessions."""

    def __init__(
        self,
        *,
        users: UserRepository,
        refresh_tokens: RefreshTokenRepository,
        session: AsyncSession,
        settings: Settings,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._users = users
        self._refresh_tokens = refresh_tokens
        self._session = session
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))

    async def login_with_google(self, identity: GoogleIdentity) -> TokenPair:
        """Create or update the user and start a new session family."""
        if not identity.email_verified:
            raise EmailNotVerified()

        user = await self._users.get_by_google_sub(identity.sub)
        if user is None:
            role = Role.admin if self._is_admin_email(identity.email) else Role.reader
            user = await self._users.create(
                google_sub=identity.sub,
                email=identity.email,
                name=identity.name,
                avatar_url=identity.avatar_url,
                role=role,
            )
        else:
            await self._users.update_profile(
                user,
                email=identity.email,
                name=identity.name,
                avatar_url=identity.avatar_url,
            )

        now = self._clock()
        family_id = uuid4()
        pair, refresh_row = self._build_token_pair(user=user, family_id=family_id, now=now)
        await self._refresh_tokens.add(refresh_row)
        await self._session.commit()
        return pair

    async def refresh(self, refresh_token: str) -> TokenPair:
        """Rotate a refresh token, revoking its family on reuse."""
        claims = decode_refresh_token(refresh_token, self._settings)
        stored = await self._refresh_tokens.get_by_hash(hash_token(refresh_token), for_update=True)
        if stored is None:
            raise InvalidToken()
        if (
            stored.id != claims.jti
            or stored.user_id != claims.user_id
            or stored.family_id != claims.family_id
        ):
            raise InvalidToken()

        now = self._clock()
        if stored.revoked_at is not None:
            await self._refresh_tokens.revoke_family(stored.family_id, revoked_at=now)
            await self._session.commit()
            raise RefreshTokenReuse()

        if stored.expires_at <= now:
            raise InvalidToken()

        user = await self._users.get_by_id(stored.user_id)
        if user is None:
            raise InvalidToken()

        pair, new_row = self._build_token_pair(user=user, family_id=stored.family_id, now=now)
        await self._refresh_tokens.add(new_row)
        await self._refresh_tokens.mark_rotated(stored, revoked_at=now, replaced_by=new_row.id)
        await self._session.commit()
        return pair

    async def logout(self, access_token: str | None) -> None:
        """Revoke the current family when the access token is validly signed."""
        if not access_token:
            return
        try:
            claims = decode_access_token(access_token, self._settings, verify_exp=False)
        except InvalidToken:
            return

        await self._refresh_tokens.revoke_family(
            claims.family_id,
            revoked_at=self._clock(),
            user_id=claims.user_id,
        )
        await self._session.commit()

    async def get_profile(self, user_id: UUID) -> User:
        """Return the current database profile for the authenticated user."""
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise AuthenticationError()
        return user

    def _is_admin_email(self, email: str) -> bool:
        normalized = email.casefold()
        return any(
            configured.casefold() == normalized for configured in self._settings.admin_emails
        )

    def _build_token_pair(
        self, *, user: User, family_id: UUID, now: datetime
    ) -> tuple[TokenPair, RefreshToken]:
        access_expires_at = now + timedelta(minutes=self._settings.access_token_ttl_minutes)
        access_token = create_access_token(
            user_id=user.id,
            role=user.role,
            family_id=family_id,
            settings=self._settings,
            issued_at=now,
            expires_at=access_expires_at,
        )

        refresh_id = uuid4()
        refresh_expires_at = now + timedelta(days=self._settings.refresh_token_ttl_days)
        refresh_token = create_refresh_token(
            user_id=user.id,
            family_id=family_id,
            jti=refresh_id,
            settings=self._settings,
            issued_at=now,
            expires_at=refresh_expires_at,
        )
        row = RefreshToken(
            id=refresh_id,
            user_id=user.id,
            family_id=family_id,
            token_hash=hash_token(refresh_token),
            expires_at=refresh_expires_at,
            created_at=now,
        )
        return TokenPair(access_token=access_token, refresh_token=refresh_token), row
