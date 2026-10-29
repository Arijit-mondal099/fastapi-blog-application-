"""Domain exceptions and their single HTTP mapping point."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.cookies import clear_oauth_state_cookie


class AppError(Exception):
    """Base error for expected application failures."""

    status_code: int = 400
    default_detail: str = "Request failed"

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.default_detail
        super().__init__(self.detail)


class AuthenticationError(AppError):
    status_code = 401
    default_detail = "Not authenticated"


class InvalidToken(AuthenticationError):
    default_detail = "Invalid or expired token"


class RefreshTokenReuse(AuthenticationError):
    default_detail = "Refresh token reuse detected"


class InvalidOAuthState(AppError):
    status_code = 400
    default_detail = "Invalid or missing OAuth state"


class OAuthError(AuthenticationError):
    default_detail = "Google authentication failed"


class OAuthExchangeError(OAuthError):
    default_detail = "Could not exchange Google authorization code"


class InvalidIdToken(OAuthError):
    default_detail = "Invalid Google ID token"


class EmailNotVerified(AppError):
    status_code = 403
    default_detail = "Google email is not verified"


class InsufficientRole(AppError):
    status_code = 403
    default_detail = "Insufficient role"


class InvalidCursor(AppError):
    status_code = 400
    default_detail = "Invalid cursor"


class UserNotFound(AppError):
    status_code = 404
    default_detail = "User not found"


class PostNotFound(AppError):
    status_code = 404
    default_detail = "Post not found"


class PostForbidden(AppError):
    status_code = 403
    default_detail = "Forbidden"


class InvalidPostState(AppError):
    status_code = 409
    default_detail = "Invalid post state"


class InvalidTagName(AppError):
    status_code = 422
    default_detail = "Tag names must be between 1 and 30 characters"


class InvalidSearchQuery(AppError):
    status_code = 422
    default_detail = "Search query must be between 1 and 200 characters"


class CommentNotFound(AppError):
    status_code = 404
    default_detail = "Comment not found"


class CommentForbidden(AppError):
    status_code = 403
    default_detail = "Forbidden"


class InvalidCommentBody(AppError):
    status_code = 422
    default_detail = "Comment body must be between 1 and 2000 characters"


class CannotDemoteSelf(AppError):
    status_code = 409
    default_detail = "Admins cannot demote themselves"


class RateLimitExceeded(AppError):
    status_code = 429
    default_detail = "Too many requests"


class RateLimitUnavailable(AppError):
    status_code = 503
    default_detail = "Rate limiter unavailable"


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render a domain error without leaking internals."""
    error = exc if isinstance(exc, AppError) else AppError()
    response = JSONResponse(
        {"detail": error.detail},
        status_code=error.status_code,
    )
    if error.status_code == 401:
        response.headers["WWW-Authenticate"] = "Bearer"
    if request.url.path == "/auth/google/callback":
        clear_oauth_state_cookie(response, get_settings())
    return response


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the shared domain-error handler."""
    app.add_exception_handler(AppError, app_error_handler)
