"""JWT creation/verification, token hashing, and OAuth state signing."""

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt

from app.core.config import Settings
from app.core.cookies import OAUTH_STATE_TTL_SECONDS
from app.core.errors import InvalidOAuthState, InvalidToken
from app.models.user import Role

JWT_ALGORITHM = "HS256"


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: UUID
    role: Role
    family_id: UUID


@dataclass(frozen=True)
class RefreshTokenClaims:
    user_id: UUID
    family_id: UUID
    jti: UUID


@dataclass(frozen=True)
class OAuthStateClaims:
    state: str
    code_verifier: str


def _secret(settings: Settings) -> str:
    return settings.jwt_secret.get_secret_value()


def create_access_token(
    *,
    user_id: UUID,
    role: Role,
    family_id: UUID,
    settings: Settings,
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> str:
    """Create a short-lived access JWT."""
    now = issued_at or datetime.now(UTC)
    expiry = expires_at or now + timedelta(minutes=settings.access_token_ttl_minutes)
    payload = {
        "sub": str(user_id),
        "role": role.value,
        "family_id": str(family_id),
        "iat": now,
        "exp": expiry,
    }
    return jwt.encode(payload, _secret(settings), algorithm=JWT_ALGORITHM)


def create_refresh_token(
    *,
    user_id: UUID,
    family_id: UUID,
    jti: UUID,
    settings: Settings,
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> str:
    """Create a refresh JWT whose hash will be stored in the database."""
    now = issued_at or datetime.now(UTC)
    expiry = expires_at or now + timedelta(days=settings.refresh_token_ttl_days)
    payload = {
        "sub": str(user_id),
        "family_id": str(family_id),
        "jti": str(jti),
        "iat": now,
        "exp": expiry,
    }
    return jwt.encode(payload, _secret(settings), algorithm=JWT_ALGORITHM)


def _decode(
    token: str,
    settings: Settings,
    *,
    verify_exp: bool,
) -> dict[str, object]:
    try:
        payload = jwt.decode(
            token,
            _secret(settings),
            algorithms=[JWT_ALGORITHM],
            options={
                "verify_exp": verify_exp,
                "require": ["sub", "iat", "exp"],
            },
        )
    except jwt.PyJWTError as exc:
        raise InvalidToken() from exc
    if not isinstance(payload, dict):
        raise InvalidToken()
    return payload


def decode_access_token(
    token: str, settings: Settings, *, verify_exp: bool = True
) -> AccessTokenClaims:
    """Verify an access JWT and return its typed claims."""
    payload = _decode(token, settings, verify_exp=verify_exp)
    try:
        user_id = UUID(str(payload["sub"]))
        role = Role(str(payload["role"]))
        family_id = UUID(str(payload["family_id"]))
    except (KeyError, ValueError) as exc:
        raise InvalidToken() from exc
    return AccessTokenClaims(user_id=user_id, role=role, family_id=family_id)


def decode_refresh_token(token: str, settings: Settings) -> RefreshTokenClaims:
    """Verify a refresh JWT and return its typed claims."""
    payload = _decode(token, settings, verify_exp=True)
    try:
        user_id = UUID(str(payload["sub"]))
        family_id = UUID(str(payload["family_id"]))
        jti = UUID(str(payload["jti"]))
    except (KeyError, ValueError) as exc:
        raise InvalidToken() from exc
    return RefreshTokenClaims(user_id=user_id, family_id=family_id, jti=jti)


def hash_token(token: str) -> str:
    """Return the SHA-256 hex digest stored for refresh tokens."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_oauth_state() -> tuple[str, str, str, datetime]:
    """Return state, PKCE verifier, PKCE challenge, and state expiry."""
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    expires_at = datetime.now(UTC) + timedelta(seconds=OAUTH_STATE_TTL_SECONDS)
    return state, verifier, challenge, expires_at


def create_oauth_state_token(
    *, state: str, code_verifier: str, settings: Settings, expires_at: datetime
) -> str:
    """Sign the state and PKCE verifier into the short-lived state cookie."""
    payload = {
        "state": state,
        "code_verifier": code_verifier,
        "iat": datetime.now(UTC),
        "exp": expires_at,
    }
    return jwt.encode(payload, _secret(settings), algorithm=JWT_ALGORITHM)


def decode_oauth_state_token(token: str, settings: Settings) -> OAuthStateClaims:
    """Verify the signed state cookie."""
    try:
        payload = jwt.decode(
            token,
            _secret(settings),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["state", "code_verifier", "iat", "exp"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidOAuthState() from exc
    state = payload.get("state")
    verifier = payload.get("code_verifier")
    if not isinstance(state, str) or not isinstance(verifier, str):
        raise InvalidOAuthState()
    return OAuthStateClaims(state=state, code_verifier=verifier)
