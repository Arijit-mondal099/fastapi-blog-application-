"""FastAPI application factory and composition root."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Request, Response
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
from app.core.logging import (
    configure_logging,
    reset_request_id,
    set_request_id,
)
from app.db.session import dispose_engine
from app.jobs.queue import create_job_queue
from app.models.user import Role


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create shared Redis access and release process resources on shutdown."""
    settings = get_settings()
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    job_queue = await create_job_queue(settings)
    app.state.job_queue = job_queue
    try:
        yield
    finally:
        await job_queue.aclose()
        await app.state.redis.aclose()
        await dispose_engine()


def create_app() -> FastAPI:
    """Create the API application."""
    settings = get_settings()
    settings.validate_startup()
    configure_logging()
    app = FastAPI(title="Blog API", version="0.1.0", lifespan=lifespan)
    register_exception_handlers(app)

    @app.middleware("http")
    async def request_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = request.headers.get("x-request-id")
        request_id = incoming if incoming and len(incoming) <= 128 else uuid4().hex
        request.state.request_id = request_id
        token = set_request_id(request_id)
        try:
            response = await call_next(request)
        finally:
            reset_request_id(token)
        response.headers["X-Request-ID"] = request_id
        return response

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
