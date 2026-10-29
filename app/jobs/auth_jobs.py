"""ARQ jobs for auth table maintenance."""

import logging
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_request_id, reset_request_id, set_request_id
from app.db.session import get_session_factory
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.repositories.users import UserRepository
from app.services.auth import AuthService

logger = logging.getLogger(__name__)


async def purge_expired_refresh_tokens(
    ctx: dict[str, Any],
    request_id: str | None = None,
) -> None:
    """Delete refresh tokens that expired or were revoked long enough ago."""
    token = set_request_id(request_id or get_request_id())
    logger.info(
        "refresh token purge job started",
        extra={"job": "purge_expired_refresh_tokens"},
    )
    try:
        async with get_session_factory()() as session:
            service = AuthService(
                users=UserRepository(session),
                refresh_tokens=RefreshTokenRepository(session),
                session=session,
                settings=get_settings(),
            )
            deleted = await service.purge_expired_refresh_tokens()
        logger.info(
            "refresh token purge job finished",
            extra={"job": "purge_expired_refresh_tokens", "deleted": deleted},
        )
    finally:
        reset_request_id(token)
