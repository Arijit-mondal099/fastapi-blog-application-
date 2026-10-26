"""Auth routes: Google login, refresh, logout, and current user."""

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.cookies import (
    ACCESS_COOKIE_NAME,
    OAUTH_STATE_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
    clear_auth_cookies,
    clear_oauth_state_cookie,
    set_auth_cookies,
    set_oauth_state_cookie,
)
from app.core.deps import CurrentUser, require_role
from app.core.errors import AuthenticationError, InvalidOAuthState
from app.core.rate_limit import enforce_auth_rate_limit
from app.core.security import (
    create_oauth_state_token,
    decode_oauth_state_token,
    generate_oauth_state,
)
from app.db.session import get_db_session
from app.integrations.google_oauth import (
    GoogleOAuthClient,
    get_google_oauth_client,
)
from app.models.user import Role, User
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.repositories.users import UserRepository
from app.schemas.auth import UserResponse
from app.services.auth import AuthService

router = APIRouter(
    prefix="/auth",
    tags=["auth"],
    dependencies=[Depends(enforce_auth_rate_limit)],
)


def get_auth_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AuthService:
    """Build the auth service for one request."""
    return AuthService(
        users=UserRepository(session),
        refresh_tokens=RefreshTokenRepository(session),
        session=session,
        settings=get_settings(),
    )


def _post_login_url(settings: Settings) -> str:
    path = settings.post_login_path.lstrip("/")
    return f"{settings.frontend_url.rstrip('/')}/{path}"


@router.get("/google/login")
async def google_login(
    google: Annotated[GoogleOAuthClient, Depends(get_google_oauth_client)],
) -> RedirectResponse:
    """Start the authorization-code flow with state and PKCE."""
    settings = get_settings()
    state, verifier, challenge, expires_at = generate_oauth_state()
    state_token = create_oauth_state_token(
        state=state,
        code_verifier=verifier,
        settings=settings,
        expires_at=expires_at,
    )
    response = RedirectResponse(
        google.authorization_url(state=state, code_challenge=challenge),
        status_code=302,
    )
    set_oauth_state_cookie(response, state_token, settings, expires_at=expires_at)
    return response


@router.get("/google/callback")
async def google_callback(
    request: Request,
    google: Annotated[GoogleOAuthClient, Depends(get_google_oauth_client)],
    service: Annotated[AuthService, Depends(get_auth_service)],
    code: str | None = None,
    state: str | None = None,
) -> RedirectResponse:
    """Validate state, exchange the code, and start a session."""
    settings = get_settings()
    stored_state = request.cookies.get(OAUTH_STATE_COOKIE_NAME)
    if not stored_state or not state or not code:
        raise InvalidOAuthState()

    state_claims = decode_oauth_state_token(stored_state, settings)
    if not hmac.compare_digest(state_claims.state, state):
        raise InvalidOAuthState()

    id_token = await google.exchange_code(code=code, code_verifier=state_claims.code_verifier)
    identity = await google.verify_id_token(id_token)
    pair = await service.login_with_google(identity)

    response = RedirectResponse(_post_login_url(settings), status_code=302)
    clear_oauth_state_cookie(response, settings)
    set_auth_cookies(response, pair.access_token, pair.refresh_token, settings)
    return response


@router.post("/refresh", status_code=204)
async def refresh(
    request: Request,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> Response:
    """Rotate the refresh token and issue a new token pair."""
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not refresh_token:
        raise AuthenticationError()

    pair = await service.refresh(refresh_token)
    response = Response(status_code=204)
    set_auth_cookies(response, pair.access_token, pair.refresh_token, get_settings())
    return response


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> Response:
    """Clear cookies and revoke the current family when possible."""
    await service.logout(request.cookies.get(ACCESS_COOKIE_NAME))
    response = Response(status_code=204)
    clear_auth_cookies(response, get_settings())
    return response


@router.get("/me", response_model=UserResponse)
async def me(
    user: Annotated[CurrentUser, Depends(require_role(Role.reader))],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> User:
    """Return the current database profile."""
    return await service.get_profile(user.id)
