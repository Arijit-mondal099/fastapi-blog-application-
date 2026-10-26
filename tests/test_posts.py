"""Post visibility, ownership, lifecycle, slug, filter, and pagination tests."""

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import Role, User
from tests.helpers import make_client
from tests.posts_helpers import auth_cookies, create_post, create_user, publish_post


async def _published_post(cookies: dict[str, str], app: FastAPI, **kwargs: str) -> dict:
    async with make_client(app, cookies) as client:
        response = await create_post(client, **kwargs)
        assert response.status_code == 201
        post = response.json()
        published = await publish_post(client, post["id"])
        assert published.status_code == 200
        return published.json()


async def test_anonymous_list_returns_only_published(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    cookies = auth_cookies(author)
    published = await _published_post(cookies, app, title="Published Post")
    async with make_client(app, cookies) as client:
        draft_response = await create_post(client, title="Draft Post")
        assert draft_response.status_code == 201
        draft = draft_response.json()

    async with make_client(app) as anonymous:
        listing = await anonymous.get("/posts")
        assert listing.status_code == 200
        items = listing.json()["items"]
        assert [item["id"] for item in items] == [published["id"]]
        assert (await anonymous.get(f"/posts/{published['slug']}")).status_code == 200
        assert (await anonymous.get(f"/posts/{draft['slug']}")).status_code == 404


async def test_draft_detail_visibility_matrix(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author_a = await create_user(db_session, Role.author)
    author_b = await create_user(db_session, Role.author)
    reader = await create_user(db_session, Role.reader)
    admin = await create_user(db_session, Role.admin)

    async with make_client(app, auth_cookies(author_a)) as client:
        response = await create_post(client, title="Secret Draft")
        assert response.status_code == 201
        draft = response.json()

    cases: list[tuple[User | None, int]] = [
        (None, 404),
        (reader, 404),
        (author_b, 404),
        (author_a, 200),
        (admin, 200),
    ]
    for viewer, expected in cases:
        cookies = auth_cookies(viewer) if viewer is not None else {}
        async with make_client(app, cookies) as client:
            response = await client.get(f"/posts/{draft['slug']}")
        assert response.status_code == expected

    async with make_client(app, auth_cookies(author_b)) as other_author:
        assert (await other_author.get("/me/posts")).json()["items"] == []


async def test_archived_detail_visibility_matrix(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author_a = await create_user(db_session, Role.author)
    author_b = await create_user(db_session, Role.author)
    reader = await create_user(db_session, Role.reader)
    admin = await create_user(db_session, Role.admin)
    cookies = auth_cookies(author_a)

    async with make_client(app, cookies) as client:
        created = await create_post(client, title="Archived Post")
        post = created.json()
        await publish_post(client, post["id"])
        archived = await client.post(f"/posts/{post['id']}/archive")
        assert archived.status_code == 200
        slug = archived.json()["slug"]

    cases: list[tuple[User | None, int]] = [
        (None, 404),
        (reader, 404),
        (author_b, 404),
        (author_a, 200),
        (admin, 200),
    ]
    for viewer, expected in cases:
        viewer_cookies = auth_cookies(viewer) if viewer is not None else {}
        async with make_client(app, viewer_cookies) as client:
            response = await client.get(f"/posts/{slug}")
        assert response.status_code == expected

    async with make_client(app) as anonymous:
        listing = await anonymous.get("/posts")
        assert listing.json()["items"] == []


async def test_owner_edits_and_non_owner_visibility_rule(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author_a = await create_user(db_session, Role.author)
    author_b = await create_user(db_session, Role.author)
    cookies_a = auth_cookies(author_a)
    cookies_b = auth_cookies(author_b)

    async with make_client(app, cookies_a) as client:
        created = await create_post(client, title="Original Title", body_md="Original body")
        assert created.status_code == 201
        post = created.json()
        edited = await client.patch(
            f"/posts/{post['id']}",
            json={"title": "Edited Title", "body_md": "Edited body"},
        )
        assert edited.status_code == 200
        assert edited.json()["title"] == "Edited Title"
        assert edited.json()["slug"] == post["slug"]

    async with make_client(app, cookies_b) as other_author:
        hidden_edit = await other_author.patch(
            f"/posts/{post['id']}",
            json={"title": "Stolen"},
        )
        assert hidden_edit.status_code == 404

    async with make_client(app, cookies_a) as client:
        published = await publish_post(client, post["id"])
        assert published.status_code == 200

    async with make_client(app, cookies_b) as other_author:
        visible_edit = await other_author.patch(
            f"/posts/{post['id']}",
            json={"title": "Stolen"},
        )
        assert visible_edit.status_code == 403
        assert (await other_author.post(f"/posts/{post['id']}/publish")).status_code == 403
        assert (await other_author.post(f"/posts/{post['id']}/archive")).status_code == 403
        assert (await other_author.delete(f"/posts/{post['id']}")).status_code == 403

    async with make_client(app, cookies_a) as client:
        detail = await client.get(f"/posts/{post['slug']}")
    assert detail.json()["title"] == "Edited Title"


async def test_admin_can_manage_any_post(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    admin = await create_user(db_session, Role.admin)
    author_cookies = auth_cookies(author)
    admin_cookies = auth_cookies(admin)

    async with make_client(app, author_cookies) as client:
        created = await create_post(client, title="Author Draft")
        post = created.json()

    async with make_client(app, admin_cookies) as admin_client:
        edited = await admin_client.patch(
            f"/posts/{post['id']}",
            json={"title": "Admin Edited"},
        )
        assert edited.status_code == 200
        assert edited.json()["title"] == "Admin Edited"
        published = await admin_client.post(f"/posts/{post['id']}/publish")
        assert published.status_code == 200
        assert published.json()["status"] == "published"
        archived = await admin_client.post(f"/posts/{post['id']}/archive")
        assert archived.status_code == 200
        assert archived.json()["status"] == "archived"
        deleted = await admin_client.delete(f"/posts/{post['id']}")
        assert deleted.status_code == 204

    async with make_client(app, author_cookies) as client:
        assert (await client.get(f"/posts/{post['slug']}")).status_code == 404


async def test_reader_cannot_create_post(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    reader = await create_user(db_session, Role.reader)
    async with make_client(app, auth_cookies(reader)) as client:
        response = await create_post(client, title="Reader Post")
    assert response.status_code == 403


async def test_slug_collision_suffixes_and_slug_is_immutable(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    cookies = auth_cookies(author)

    async with make_client(app, cookies) as client:
        first = await create_post(client, title="Same Title")
        second = await create_post(client, title="Same Title")
        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["slug"] == "same-title"
        second_post = second.json()
        assert second_post["slug"] == "same-title-2"

        edited = await client.patch(
            f"/posts/{second_post['id']}",
            json={"title": "A Completely Different Title"},
        )
        assert edited.status_code == 200
        assert edited.json()["slug"] == "same-title-2"


async def test_publish_timestamp_is_set_once_and_preserved(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    cookies = auth_cookies(author)

    async with make_client(app, cookies) as client:
        created = await create_post(client, title="Timestamped Post")
        post = created.json()
        first_publish = await publish_post(client, post["id"])
        assert first_publish.status_code == 200
        published_at = first_publish.json()["published_at"]
        assert published_at is not None

        second_publish = await publish_post(client, post["id"])
        assert second_publish.json()["published_at"] == published_at

        archived = await client.post(f"/posts/{post['id']}/archive")
        assert archived.status_code == 200
        assert archived.json()["published_at"] == published_at

        restored = await publish_post(client, post["id"])
        assert restored.status_code == 200
        assert restored.json()["published_at"] == published_at


async def test_cursor_pagination_has_no_gaps_or_duplicates_across_inserts(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    cookies = auth_cookies(author)
    original_ids: set[str] = set()

    async with make_client(app, cookies) as client:
        for index in range(5):
            created = await create_post(client, title=f"Paged Post {index}")
            post = created.json()
            original_ids.add(post["id"])
            await publish_post(client, post["id"])

    async with make_client(app) as anonymous:
        first = await anonymous.get("/posts", params={"limit": 2})
        assert first.status_code == 200
        first_items = first.json()["items"]
        collected = list(first_items)
        cursor = first.json()["next_cursor"]
        assert cursor is not None

        async with make_client(app, cookies) as author_client:
            inserted = await create_post(author_client, title="Inserted Between Pages")
            inserted_post = inserted.json()
            await publish_post(author_client, inserted_post["id"])

        while cursor is not None:
            page = await anonymous.get("/posts", params={"limit": 2, "cursor": cursor})
            assert page.status_code == 200
            collected.extend(page.json()["items"])
            cursor = page.json()["next_cursor"]

    ids = [item["id"] for item in collected]
    assert len(ids) == len(set(ids))
    assert set(ids) == original_ids
    assert inserted_post["id"] not in ids


async def test_tag_and_author_filters(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author_a = await create_user(db_session, Role.author)
    author_b = await create_user(db_session, Role.author)
    fastapi_a = await _published_post(
        auth_cookies(author_a),
        app,
        title="A FastAPI",
        tags=["FastAPI"],
    )
    python_a = await _published_post(
        auth_cookies(author_a),
        app,
        title="A Python",
        tags=["python"],
    )
    fastapi_b = await _published_post(
        auth_cookies(author_b),
        app,
        title="B FastAPI",
        tags=["fastapi"],
    )
    async with make_client(app, auth_cookies(author_a)) as client:
        await create_post(client, title="A Draft FastAPI", tags=["FastAPI"])

    async with make_client(app) as anonymous:
        by_tag = await anonymous.get("/posts", params={"tag": "fastapi"})
        by_author_a = await anonymous.get("/posts", params={"author": str(author_a.id)})
        by_author_b = await anonymous.get("/posts", params={"author": str(author_b.id)})
        combined = await anonymous.get(
            "/posts",
            params={"tag": "fastapi", "author": str(author_a.id)},
        )

    assert {item["id"] for item in by_tag.json()["items"]} == {
        fastapi_a["id"],
        fastapi_b["id"],
    }
    assert {item["id"] for item in by_author_a.json()["items"]} == {
        fastapi_a["id"],
        python_a["id"],
    }
    assert {item["id"] for item in by_author_b.json()["items"]} == {fastapi_b["id"]}
    assert {item["id"] for item in combined.json()["items"]} == {fastapi_a["id"]}


async def test_me_posts_returns_own_posts_in_all_statuses(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author_a = await create_user(db_session, Role.author)
    author_b = await create_user(db_session, Role.author)
    reader = await create_user(db_session, Role.reader)
    cookies_a = auth_cookies(author_a)

    async with make_client(app, cookies_a) as client:
        draft = (await create_post(client, title="Mine Draft")).json()
        published = (await create_post(client, title="Mine Published")).json()
        await publish_post(client, published["id"])
        archived = (await create_post(client, title="Mine Archived")).json()
        await publish_post(client, archived["id"])
        await client.post(f"/posts/{archived['id']}/archive")

    async with make_client(app, auth_cookies(author_b)) as other:
        await create_post(other, title="Other Draft")

    async with make_client(app, cookies_a) as client:
        response = await client.get("/me/posts")
        assert response.status_code == 200
        items = response.json()["items"]

    assert {item["id"] for item in items} == {draft["id"], published["id"], archived["id"]}
    assert {item["status"] for item in items} == {"draft", "published", "archived"}

    async with make_client(app, auth_cookies(author_b)) as other:
        other_items = (await other.get("/me/posts")).json()["items"]
    assert len(other_items) == 1
    assert other_items[0]["id"] != draft["id"]

    async with make_client(app) as anonymous:
        assert (await anonymous.get("/me/posts")).status_code == 401
    async with make_client(app, auth_cookies(reader)) as reader_client:
        assert (await reader_client.get("/me/posts")).status_code == 403


async def test_invalid_cursor_returns_400(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)

    async with make_client(app) as anonymous:
        listing = await anonymous.get("/posts", params={"cursor": "not-a-cursor"})
    assert listing.status_code == 400

    async with make_client(app, auth_cookies(author)) as client:
        mine = await client.get("/me/posts", params={"cursor": "not-a-cursor"})
    assert mine.status_code == 400
