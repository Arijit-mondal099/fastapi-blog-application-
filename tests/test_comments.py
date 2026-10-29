"""Comment visibility, creation, validation, deletion, and pagination tests."""

import asyncio
from uuid import UUID

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.comment import Comment
from app.models.user import Role, User
from tests.helpers import make_client
from tests.posts_helpers import auth_cookies, create_post, create_user, publish_post


async def _published_post(client, *, title: str = "Commentable Post") -> dict:
    response = await create_post(client, title=title)
    assert response.status_code == 201
    post = response.json()
    published = await publish_post(client, post["id"])
    assert published.status_code == 200
    return published.json()


async def _create_comment(client, *, slug: str, body: str) -> dict:
    response = await client.post(f"/posts/{slug}/comments", json={"body": body})
    assert response.status_code == 201, response.text
    return response.json()


async def test_anonymous_list_returns_comments_oldest_first(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    reader = await create_user(db_session, Role.reader)
    async with make_client(app, auth_cookies(author)) as client:
        post = await _published_post(client)

    bodies = ["First", "Second", "Third"]
    for body in bodies:
        async with make_client(app, auth_cookies(reader)) as commenter:
            response = await commenter.post(
                f"/posts/{post['slug']}/comments",
                json={"body": body},
            )
            assert response.status_code == 201
        await asyncio.sleep(0.01)

    async with make_client(app) as anonymous:
        response = await anonymous.get(f"/posts/{post['slug']}/comments")

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["body"] for item in items] == bodies
    stamps = [(item["created_at"], item["id"]) for item in items]
    assert stamps == sorted(stamps)
    assert response.json()["next_cursor"] is None


async def test_anonymous_create_returns_401(app: FastAPI, db_session: AsyncSession) -> None:
    author = await create_user(db_session, Role.author)
    async with make_client(app, auth_cookies(author)) as client:
        post = await _published_post(client)

    async with make_client(app) as anonymous:
        response = await anonymous.post(
            f"/posts/{post['slug']}/comments",
            json={"body": "No session"},
        )

    assert response.status_code == 401


async def test_reader_author_and_admin_can_comment_on_published_post(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    post_author = await create_user(db_session, Role.author)
    commenters: dict[Role, User] = {
        Role.reader: await create_user(db_session, Role.reader, name="Reader"),
        Role.author: await create_user(db_session, Role.author, name="Other Author"),
        Role.admin: await create_user(db_session, Role.admin, name="Admin"),
    }
    async with make_client(app, auth_cookies(post_author)) as client:
        post = await _published_post(client)

    for role, commenter in commenters.items():
        async with make_client(app, auth_cookies(commenter)) as client:
            response = await client.post(
                f"/posts/{post['slug']}/comments",
                json={"body": f"From {role.value}"},
            )
            assert response.status_code == 201
            assert response.json()["body"] == f"From {role.value}"
            assert response.json()["author_id"] == str(commenter.id)
            assert response.json()["post_id"] == post["id"]
        await asyncio.sleep(0.01)

    async with make_client(app) as anonymous:
        listing = await anonymous.get(f"/posts/{post['slug']}/comments")
    assert [item["body"] for item in listing.json()["items"]] == [
        "From reader",
        "From author",
        "From admin",
    ]


@pytest.mark.parametrize("archived", [False, True])
async def test_hidden_post_comments_visibility_and_create_rule(
    app: FastAPI,
    db_session: AsyncSession,
    archived: bool,
) -> None:
    author = await create_user(db_session, Role.author)
    reader = await create_user(db_session, Role.reader)
    other_author = await create_user(db_session, Role.author)
    admin = await create_user(db_session, Role.admin)

    async with make_client(app, auth_cookies(author)) as client:
        created = await create_post(client, title="Hidden Commentable")
        assert created.status_code == 201
        post = created.json()
        existing_bodies: list[str] = []
        if archived:
            published = await publish_post(client, post["id"])
            assert published.status_code == 200
            post = published.json()
            await _create_comment(client, slug=post["slug"], body="Before archive")
            existing_bodies = ["Before archive"]
            archived_response = await client.post(f"/posts/{post['id']}/archive")
            assert archived_response.status_code == 200
            post = archived_response.json()

    hidden: list[tuple[User | None, int]] = [
        (None, 404),
        (reader, 404),
        (other_author, 404),
    ]
    for viewer, expected in hidden:
        cookies = auth_cookies(viewer) if viewer is not None else {}
        async with make_client(app, cookies) as client:
            listing = await client.get(f"/posts/{post['slug']}/comments")
            assert listing.status_code == expected
            create = await client.post(
                f"/posts/{post['slug']}/comments",
                json={"body": "Should not land"},
            )
            assert create.status_code == (401 if viewer is None else 404)

    for viewer in (author, admin):
        async with make_client(app, auth_cookies(viewer)) as client:
            listing = await client.get(f"/posts/{post['slug']}/comments")
            assert listing.status_code == 200
            assert [item["body"] for item in listing.json()["items"]] == existing_bodies
            create = await client.post(
                f"/posts/{post['slug']}/comments",
                json={"body": "Not on hidden posts"},
            )
            assert create.status_code == 404


@pytest.mark.parametrize("body", ["", "   ", "x" * 2001])
async def test_empty_or_overlong_bodies_return_422(
    app: FastAPI,
    db_session: AsyncSession,
    body: str,
) -> None:
    author = await create_user(db_session, Role.author)
    reader = await create_user(db_session, Role.reader)
    async with make_client(app, auth_cookies(author)) as client:
        post = await _published_post(client)

    async with make_client(app, auth_cookies(reader)) as client:
        response = await client.post(f"/posts/{post['slug']}/comments", json={"body": body})
    assert response.status_code == 422

    stored = await db_session.scalar(select(func.count()).select_from(Comment))
    assert stored == 0


async def test_two_thousand_character_body_succeeds_and_is_trimmed(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    reader = await create_user(db_session, Role.reader)
    async with make_client(app, auth_cookies(author)) as client:
        post = await _published_post(client)

    async with make_client(app, auth_cookies(reader)) as client:
        response = await client.post(
            f"/posts/{post['slug']}/comments",
            json={"body": "  " + "x" * 2000 + "  "},
        )
    assert response.status_code == 201
    assert response.json()["body"] == "x" * 2000


async def test_html_body_is_stored_and_returned_as_literal_text(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    reader = await create_user(db_session, Role.reader)
    async with make_client(app, auth_cookies(author)) as client:
        post = await _published_post(client)
    body = '<p>Hello <strong>world</strong></p><script>alert("x")</script>'

    async with make_client(app, auth_cookies(reader)) as client:
        created = await _create_comment(client, slug=post["slug"], body=body)
    assert created["body"] == body

    async with make_client(app) as anonymous:
        listing = await anonymous.get(f"/posts/{post['slug']}/comments")
    assert listing.status_code == 200
    assert listing.json()["items"][0]["body"] == body


async def test_comment_author_can_delete_own_comment(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    reader = await create_user(db_session, Role.reader)
    async with make_client(app, auth_cookies(author)) as client:
        post = await _published_post(client)
    async with make_client(app, auth_cookies(reader)) as client:
        comment = await _create_comment(client, slug=post["slug"], body="Mine")

    async with make_client(app, auth_cookies(reader)) as client:
        deleted = await client.delete(f"/comments/{comment['id']}")
        assert deleted.status_code == 204
        second = await client.delete(f"/comments/{comment['id']}")
        assert second.status_code == 404

    async with make_client(app) as anonymous:
        listing = await anonymous.get(f"/posts/{post['slug']}/comments")
    assert listing.json()["items"] == []


async def test_post_author_can_delete_comment_on_own_post(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    reader = await create_user(db_session, Role.reader)
    async with make_client(app, auth_cookies(author)) as client:
        post = await _published_post(client)
    async with make_client(app, auth_cookies(reader)) as client:
        comment = await _create_comment(client, slug=post["slug"], body="Yours now")

    async with make_client(app, auth_cookies(author)) as client:
        deleted = await client.delete(f"/comments/{comment['id']}")
        assert deleted.status_code == 204
        assert (await client.delete(f"/comments/{comment['id']}")).status_code == 404


async def test_admin_can_delete_any_comment(app: FastAPI, db_session: AsyncSession) -> None:
    author = await create_user(db_session, Role.author)
    reader = await create_user(db_session, Role.reader)
    admin = await create_user(db_session, Role.admin)
    async with make_client(app, auth_cookies(author)) as client:
        post = await _published_post(client)
    async with make_client(app, auth_cookies(reader)) as client:
        comment = await _create_comment(client, slug=post["slug"], body="Admin target")

    async with make_client(app, auth_cookies(admin)) as client:
        deleted = await client.delete(f"/comments/{comment['id']}")
        assert deleted.status_code == 204
        assert (await client.delete(f"/comments/{comment['id']}")).status_code == 404


async def test_unrelated_callers_cannot_delete_comment(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    other_author = await create_user(db_session, Role.author)
    comment_author = await create_user(db_session, Role.reader)
    other_reader = await create_user(db_session, Role.reader)
    async with make_client(app, auth_cookies(author)) as client:
        post = await _published_post(client)
    async with make_client(app, auth_cookies(comment_author)) as client:
        comment = await _create_comment(client, slug=post["slug"], body="Stay")

    async with make_client(app) as anonymous:
        assert (await anonymous.delete(f"/comments/{comment['id']}")).status_code == 401
    async with make_client(app, auth_cookies(other_reader)) as client:
        assert (await client.delete(f"/comments/{comment['id']}")).status_code == 403
    async with make_client(app, auth_cookies(other_author)) as client:
        assert (await client.delete(f"/comments/{comment['id']}")).status_code == 403

    async with make_client(app) as anonymous:
        listing = await anonymous.get(f"/posts/{post['slug']}/comments")
    assert [item["body"] for item in listing.json()["items"]] == ["Stay"]


async def test_deleting_post_removes_its_comments(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    async with make_client(app, auth_cookies(author)) as client:
        post = await _published_post(client)
        await _create_comment(client, slug=post["slug"], body="Gone with the post")
        deleted = await client.delete(f"/posts/{post['id']}")
        assert deleted.status_code == 204

    async with make_client(app) as anonymous:
        assert (await anonymous.get(f"/posts/{post['slug']}/comments")).status_code == 404
    remaining = await db_session.scalar(
        select(func.count()).select_from(Comment).where(Comment.post_id == UUID(post["id"]))
    )
    assert remaining == 0


async def test_cursor_pagination_has_no_duplicates_or_gaps_across_inserts(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    reader = await create_user(db_session, Role.reader)
    async with make_client(app, auth_cookies(author)) as client:
        post = await _published_post(client)

    original_ids: set[str] = set()
    for index in range(5):
        async with make_client(app, auth_cookies(reader)) as client:
            comment = await _create_comment(
                client,
                slug=post["slug"],
                body=f"Paged comment {index}",
            )
        original_ids.add(comment["id"])
        await asyncio.sleep(0.01)

    async with make_client(app) as anonymous:
        first = await anonymous.get(f"/posts/{post['slug']}/comments", params={"limit": 2})
        assert first.status_code == 200
        first_items = list(first.json()["items"])
        collected = list(first_items)
        cursor = first.json()["next_cursor"]
        assert cursor is not None

        async with make_client(app, auth_cookies(reader)) as client:
            inserted = await _create_comment(
                client,
                slug=post["slug"],
                body="Inserted between pages",
            )

        while cursor is not None:
            page = await anonymous.get(
                f"/posts/{post['slug']}/comments",
                params={"limit": 2, "cursor": cursor},
            )
            assert page.status_code == 200
            collected.extend(page.json()["items"])
            cursor = page.json()["next_cursor"]

        full = await anonymous.get(f"/posts/{post['slug']}/comments")

    collected_ids = [item["id"] for item in collected]
    full_ids = [item["id"] for item in full.json()["items"]]
    assert len(collected_ids) == len(set(collected_ids))
    assert set(collected_ids) == original_ids | {inserted["id"]}
    assert collected_ids == full_ids
    assert collected_ids[: len(first_items)] == [item["id"] for item in first_items]
    assert collected_ids[-1] == inserted["id"]


async def test_limit_is_capped_at_50(app: FastAPI, db_session: AsyncSession) -> None:
    author = await create_user(db_session, Role.author)
    async with make_client(app, auth_cookies(author)) as client:
        post = await _published_post(client)

    async with make_client(app) as anonymous:
        over_cap = await anonymous.get(
            f"/posts/{post['slug']}/comments",
            params={"limit": 51},
        )
        at_cap = await anonymous.get(f"/posts/{post['slug']}/comments", params={"limit": 50})
        default_page = await anonymous.get(f"/posts/{post['slug']}/comments")

    assert over_cap.status_code == 422
    assert at_cap.status_code == 200
    assert default_page.status_code == 200


async def test_invalid_cursor_returns_400(app: FastAPI, db_session: AsyncSession) -> None:
    author = await create_user(db_session, Role.author)
    async with make_client(app, auth_cookies(author)) as client:
        post = await _published_post(client)
        await _create_comment(client, slug=post["slug"], body="Cursor seed")

    async with make_client(app) as anonymous:
        response = await anonymous.get(
            f"/posts/{post['slug']}/comments",
            params={"cursor": "not-a-cursor"},
        )
    assert response.status_code == 400
