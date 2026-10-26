"""Tag normalization, counting, validation, and deletion tests."""

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.post import PostTag, Tag
from app.models.user import Role
from tests.helpers import make_client
from tests.posts_helpers import auth_cookies, create_post, create_user, publish_post


async def test_tags_are_case_insensitive_deduplicated_and_count_published(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    cookies = auth_cookies(author)

    async with make_client(app, cookies) as client:
        first = await create_post(
            client,
            title="Tagged Post",
            tags=["FastAPI", "fastapi", " Python "],
        )
        assert first.status_code == 201
        first_post = first.json()
        assert {tag["name"].casefold() for tag in first_post["tags"]} == {
            "fastapi",
            "python",
        }
        await publish_post(client, first_post["id"])

        second = await create_post(client, title="Second Tagged Post", tags=["FASTAPI"])
        assert second.status_code == 201
        await publish_post(client, second.json()["id"])

        draft = await create_post(client, title="Draft Tagged Post", tags=["FastAPI"])
        assert draft.status_code == 201

        archived = await create_post(client, title="Archived Tagged Post", tags=["Python"])
        assert archived.status_code == 201
        await publish_post(client, archived.json()["id"])
        await client.post(f"/posts/{archived.json()['id']}/archive")

    async with make_client(app) as anonymous:
        response = await anonymous.get("/tags")
    assert response.status_code == 200
    counts = {tag["slug"]: tag["post_count"] for tag in response.json()}
    assert counts == {"fastapi": 2, "python": 1}

    tag_count = await db_session.scalar(select(func.count()).select_from(Tag))
    assert tag_count == 2


async def test_deleting_post_removes_links_but_keeps_tag_rows(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    cookies = auth_cookies(author)

    async with make_client(app, cookies) as client:
        created = await create_post(client, title="Delete Me", tags=["Keep Me"])
        assert created.status_code == 201
        post = created.json()
        assert len(post["tags"]) == 1
        response = await client.delete(f"/posts/{post['id']}")
        assert response.status_code == 204

    link_count = await db_session.scalar(select(func.count()).select_from(PostTag))
    tag_count = await db_session.scalar(select(func.count()).select_from(Tag))
    assert link_count == 0
    assert tag_count == 1

    async with make_client(app) as anonymous:
        tags = await anonymous.get("/tags")
    assert tags.status_code == 200
    assert tags.json() == [
        {
            "id": post["tags"][0]["id"],
            "name": "Keep Me",
            "slug": "keep-me",
            "post_count": 0,
        }
    ]


@pytest.mark.parametrize("invalid_name", ["", "   ", "x" * 31])
async def test_empty_or_overlong_tag_names_return_422(
    app: FastAPI,
    db_session: AsyncSession,
    invalid_name: str,
) -> None:
    author = await create_user(db_session, Role.author)
    async with make_client(app, auth_cookies(author)) as client:
        response = await create_post(client, title="Invalid Tag", tags=[invalid_name])
    assert response.status_code == 422


async def test_thirty_character_tag_name_is_accepted(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    async with make_client(app, auth_cookies(author)) as client:
        response = await create_post(client, title="Boundary Tag", tags=["y" * 30])
    assert response.status_code == 201
    assert response.json()["tags"][0]["name"] == "y" * 30
