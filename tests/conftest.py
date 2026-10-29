"""Shared fixtures for Phase 1 tests."""

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from testcontainers.community.postgres import PostgresContainer

# Set a valid test configuration before importing the application module, which
# builds the module-level FastAPI app and validates JWT_SECRET at import time.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-" + "x" * 32)
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("FRONTEND_URL", "https://frontend.test")
os.environ.setdefault("POST_LOGIN_PATH", "/")
os.environ.setdefault("ADMIN_EMAILS", "admin@example.com")
os.environ.setdefault("IMAGEKIT_PUBLIC_KEY", "test-imagekit-public-key")
os.environ.setdefault("IMAGEKIT_PRIVATE_KEY", "test-imagekit-private-key")
os.environ.setdefault("IMAGEKIT_URL_ENDPOINT", "https://ik.imagekit.io/test")

from app.core.config import get_settings  # noqa: E402
from app.db.session import get_db_session  # noqa: E402
from app.integrations.google_oauth import get_google_oauth_client  # noqa: E402
from app.main import create_app  # noqa: E402
from tests.helpers import FakeGoogleClient, FakeRedis  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _run_migrations(database_url: str, revision: str = "head") -> None:
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    get_settings.cache_clear()
    try:
        config = Config(str(ROOT / "alembic.ini"))
        command.upgrade(config, revision)
    finally:
        get_settings.cache_clear()
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """Start one real Postgres container and migrate it to head."""
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as container:
        url = container.get_connection_url()
        _run_migrations(url)
        yield url


@pytest_asyncio.fixture
async def db_engine(postgres_url: str) -> AsyncIterator[AsyncEngine]:
    """Create an isolated engine for each test and clear auth tables."""
    engine = create_async_engine(postgres_url, poolclass=NullPool)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE TABLE post_tags, comments, posts, media, tags, refresh_tokens, users "
                "RESTART IDENTITY CASCADE"
            )
        )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
def fake_google() -> FakeGoogleClient:
    return FakeGoogleClient()


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture(autouse=True)
def auth_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Apply deterministic auth settings for every test."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-" + "x" * 32)
    monkeypatch.setenv("ACCESS_TOKEN_TTL_MINUTES", "15")
    monkeypatch.setenv("REFRESH_TOKEN_TTL_DAYS", "30")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("FRONTEND_URL", "https://frontend.test")
    monkeypatch.setenv("POST_LOGIN_PATH", "/")
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    monkeypatch.setenv("COOKIE_DOMAIN", "")
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", "0")
    monkeypatch.setenv("IMAGEKIT_PUBLIC_KEY", "test-imagekit-public-key")
    monkeypatch.setenv("IMAGEKIT_PRIVATE_KEY", "test-imagekit-private-key")
    monkeypatch.setenv("IMAGEKIT_URL_ENDPOINT", "https://ik.imagekit.io/test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    get_google_oauth_client.cache_clear()


@pytest_asyncio.fixture
async def app(
    db_session: AsyncSession,
    fake_google: FakeGoogleClient,
    fake_redis: FakeRedis,
) -> AsyncIterator[FastAPI]:
    application = create_app()

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    application.dependency_overrides[get_db_session] = override_session
    application.dependency_overrides[get_google_oauth_client] = lambda: fake_google
    application.state.redis = fake_redis
    try:
        yield application
    finally:
        application.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as test_client:
        yield test_client
