"""Migration backfill test for posts.search_vector."""

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import asyncpg
import pytest
from sqlalchemy import make_url, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import _run_migrations


@dataclass(frozen=True)
class PreSearchDatabase:
    """A migrated database containing one post from before the search revision."""

    url: str
    post_id: UUID


async def _admin_statement(base_url: URL, statement: str) -> None:
    dsn = base_url.set(drivername="postgresql").render_as_string(hide_password=False)
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(statement)
    finally:
        await connection.close()


async def _seed_pre_search_post(database_url: str) -> UUID:
    engine = create_async_engine(database_url, poolclass=NullPool)
    user_id = uuid4()
    post_id = uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO users (id, google_sub, email)
                    VALUES (:user_id, :google_sub, :email)
                    """
                ),
                {
                    "user_id": user_id,
                    "google_sub": f"sub-{user_id}",
                    "email": f"{user_id}@example.com",
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO posts (
                        id, author_id, slug, title, body_md, status, published_at
                    )
                    VALUES (
                        :post_id,
                        :author_id,
                        'legacy-search-post',
                        'LegacyTitleMarker',
                        'LegacyBodyMarker text',
                        'published'::post_status,
                        now()
                    )
                    """
                ),
                {"post_id": post_id, "author_id": user_id},
            )
    finally:
        await engine.dispose()
    return post_id


@pytest.fixture(scope="module")
def pre_search_database(postgres_url: str) -> Iterator[PreSearchDatabase]:
    """Create an isolated database at revision 0003, then upgrade it to head."""
    base_url = make_url(postgres_url)
    database_name = f"search_backfill_{uuid4().hex}"
    asyncio.run(_admin_statement(base_url, f'CREATE DATABASE "{database_name}"'))
    database_url = base_url.set(database=database_name).render_as_string(hide_password=False)
    try:
        _run_migrations(database_url, "0003_posts_and_tags")
        post_id = asyncio.run(_seed_pre_search_post(database_url))
        _run_migrations(database_url)
        yield PreSearchDatabase(url=database_url, post_id=post_id)
    finally:
        asyncio.run(
            _admin_statement(
                base_url,
                f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)',
            )
        )


async def test_migration_backfills_search_vector_for_existing_posts(
    pre_search_database: PreSearchDatabase,
) -> None:
    engine = create_async_engine(pre_search_database.url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        """
                        SELECT
                            search_vector IS NOT NULL,
                            search_vector @@ websearch_to_tsquery(
                                'english',
                                'legacytitlemarker'
                            ),
                            search_vector @@ websearch_to_tsquery(
                                'english',
                                'legacybodymarker'
                            )
                        FROM posts
                        WHERE id = :post_id
                        """
                    ),
                    {"post_id": pre_search_database.post_id},
                )
            ).one()
    finally:
        await engine.dispose()

    assert tuple(row) == (True, True, True)
