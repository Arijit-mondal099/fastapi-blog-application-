"""Cookie names, paths, and helpers for auth and OAuth state cookies."""

from datetime import UTC, datetime

from starlette.responses import Response

from app.core.config import Settings

ACCESS_COOKIE_NAME = "access_token"
REFRESH_COOKIE_NAME = "refresh_token"
OAUTH_STATE_COOKIE_NAME = "oauth_state"

ACCESS_COOKIE_PATH = "/"
REFRESH_COOKIE_PATH = "/auth/refresh"
OAUTH_STATE_COOKIE_PATH = "/auth/google"
OAUTH_STATE_TTL_SECONDS = 10 * 60


def _cookie_domain(settings: Settings) -> str | None:
    return settings.cookie_domain or None


def set_access_cookie(response: Response, token: str, settings: Settings) -> None:
    """Set the short-lived access cookie."""
    response.set_cookie(
        ACCESS_COOKIE_NAME,
        token,
        max_age=settings.access_token_ttl_minutes * 60,
        path=ACCESS_COOKIE_PATH,
        domain=_cookie_domain(settings),
        secure=True,
        httponly=True,
        samesite="strict",
    )


def set_refresh_cookie(response: Response, token: str, settings: Settings) -> None:
    """Set the refresh cookie, scoped to the refresh endpoint only."""
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        token,
        max_age=settings.refresh_token_ttl_days * 24 * 60 * 60,
        path=REFRESH_COOKIE_PATH,
        domain=_cookie_domain(settings),
        secure=True,
        httponly=True,
        samesite="strict",
    )


def set_auth_cookies(
    response: Response, access_token: str, refresh_token: str, settings: Settings
) -> None:
    """Set both auth cookies."""
    set_access_cookie(response, access_token, settings)
    set_refresh_cookie(response, refresh_token, settings)


def set_oauth_state_cookie(
    response: Response, token: str, settings: Settings, *, expires_at: datetime
) -> None:
    """Set the short-lived Lax state cookie needed across Google's redirect."""
    max_age = max(0, int((expires_at - datetime.now(UTC)).total_seconds()))
    response.set_cookie(
        OAUTH_STATE_COOKIE_NAME,
        token,
        max_age=max_age,
        path=OAUTH_STATE_COOKIE_PATH,
        domain=_cookie_domain(settings),
        secure=True,
        httponly=True,
        samesite="lax",
    )


def clear_oauth_state_cookie(response: Response, settings: Settings) -> None:
    """Remove the OAuth state cookie."""
    response.delete_cookie(
        OAUTH_STATE_COOKIE_NAME,
        path=OAUTH_STATE_COOKIE_PATH,
        domain=_cookie_domain(settings),
        secure=True,
        httponly=True,
        samesite="lax",
    )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    """Remove both auth cookies using their original paths."""
    response.delete_cookie(
        ACCESS_COOKIE_NAME,
        path=ACCESS_COOKIE_PATH,
        domain=_cookie_domain(settings),
        secure=True,
        httponly=True,
        samesite="strict",
    )
    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        domain=_cookie_domain(settings),
        secure=True,
        httponly=True,
        samesite="strict",
    )
