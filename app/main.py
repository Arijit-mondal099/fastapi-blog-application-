"""FastAPI application factory and composition root."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from redis.asyncio import Redis

from app.api.v1.admin import router as admin_router
from app.api.v1.auth import router as auth_router
from app.api.v1.comments import router as comments_router
from app.api.v1.media import router as media_router
from app.api.v1.posts import router as posts_router
from app.api.v1.search import router as search_router
from app.api.v1.tags import router as tags_router
from app.core.config import get_settings
from app.core.deps import CurrentUser, require_role
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.db.session import dispose_engine
from app.models.user import Role


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create shared Redis access and release process resources on shutdown."""
    settings = get_settings()
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        yield
    finally:
        await app.state.redis.aclose()
        await dispose_engine()


def create_app() -> FastAPI:
    """Create the API application."""
    settings = get_settings()
    settings.validate_startup()
    configure_logging()
    app = FastAPI(title="Blog API", version="0.1.0", lifespan=lifespan)
    register_exception_handlers(app)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(posts_router)
    app.include_router(search_router)
    app.include_router(tags_router)
    app.include_router(comments_router)
    app.include_router(media_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    if settings.app_env == "test":

        @app.post("/test/admin-only")
        async def admin_only(
            user: Annotated[CurrentUser, Depends(require_role(Role.admin))],
        ) -> dict[str, str]:
            return {"status": "ok", "role": user.role.value}

        @app.post("/test/author-only")
        async def author_only(
            user: Annotated[CurrentUser, Depends(require_role(Role.author))],
        ) -> dict[str, str]:
            return {"status": "ok", "role": user.role.value}

    return app


app = create_app()
