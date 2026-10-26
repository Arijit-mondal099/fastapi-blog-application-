"""Public search, ranking, triggers, cursors, and query-plan tests."""

import base64
import json
from uuid import UUID

import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.post import Post
from app.models.user import Role
from tests.helpers import make_client
from tests.posts_helpers import auth_cookies, create_post, create_user, publish_post


async def _published_post(
    client,
    *,
    title: str,
    body_md: str = "Body",
    tags: list[str] | None = None,
) -> dict:
    response = await create_post(client, title=title, body_md=body_md, tags=tags)
    assert response.status_code == 201
    post = response.json()
    published = await publish_post(client, post["id"])
    assert published.status_code == 200
    return published.json()


def _cursor_with_payload(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


async def test_search_returns_published_matches_and_excludes_drafts(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    cookies = auth_cookies(author)
    async with make_client(app, cookies) as client:
        published = await _published_post(
            client,
            title="Published Searchable",
            body_md="Visible searchable body",
        )
        draft_response = await create_post(
            client,
            title="Draft Searchable",
            body_md="Hidden searchable body",
        )
        assert draft_response.status_code == 201

    async with make_client(app) as anonymous:
        response = await anonymous.get("/search", params={"q": "searchable"})

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [published["id"]]
    item = body["items"][0]
    assert set(item) == {
        "id",
        "title",
        "slug",
        "excerpt",
        "published_at",
        "rank",
    }
    assert item["excerpt"] == "Visible searchable body"
    assert item["published_at"] == published["published_at"]
    assert body["next_cursor"] is None


async def test_search_excludes_archived_matches(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    cookies = auth_cookies(author)
    async with make_client(app, cookies) as client:
        archived = await _published_post(
            client,
            title="Archived Searchable",
            body_md="Archived searchable body",
        )
        response = await client.post(f"/posts/{archived['id']}/archive")
        assert response.status_code == 200

    async with make_client(app) as anonymous:
        response = await anonymous.get("/search", params={"q": "archived"})

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}


async def test_title_match_ranks_above_body_only_match(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    cookies = auth_cookies(author)
    async with make_client(app, cookies) as client:
        title_match = await _published_post(
            client,
            title="Ranking Needle",
            body_md="Ordinary text",
        )
        body_match = await _published_post(
            client,
            title="Ordinary Title",
            body_md="Ranking Needle appears once",
        )

    async with make_client(app) as anonymous:
        response = await anonymous.get("/search", params={"q": "needle"})

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["id"] for item in items] == [title_match["id"], body_match["id"]]
    assert items[0]["rank"] > items[1]["rank"]


async def test_websearch_syntax_accepts_phrases_and_exclusions(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    cookies = auth_cookies(author)
    async with make_client(app, cookies) as client:
        await _published_post(
            client,
            title="FastAPI Phrase Guide",
            body_md="Python framework for APIs",
        )

    async with make_client(app) as anonymous:
        phrase = await anonymous.get("/search", params={"q": '"fastapi phrase"'})
        exclusion = await anonymous.get("/search", params={"q": "fastapi -flask"})

    assert phrase.status_code == 200
    assert [item["title"] for item in phrase.json()["items"]] == ["FastAPI Phrase Guide"]
    assert exclusion.status_code == 200
    assert [item["title"] for item in exclusion.json()["items"]] == ["FastAPI Phrase Guide"]


@pytest.mark.parametrize("query", ["the and of", "...", "!!!"])
async def test_stop_words_and_punctuation_return_empty_page(
    app: FastAPI,
    query: str,
) -> None:
    async with make_client(app) as anonymous:
        response = await anonymous.get("/search", params={"q": query})

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}


@pytest.mark.parametrize("query", ["", "   ", "x" * 201])
async def test_empty_whitespace_or_overlong_query_returns_422(
    app: FastAPI,
    query: str,
) -> None:
    async with make_client(app) as anonymous:
        response = await anonymous.get("/search", params={"q": query})

    assert response.status_code == 422


async def test_search_vector_trigger_tracks_insert_updates_and_publish(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    cookies = auth_cookies(author)

    async with make_client(app, cookies) as client:
        created = await create_post(
            client,
            title="AlphaTrigger title",
            body_md="BetaTrigger body",
        )
        assert created.status_code == 201
        post_id = UUID(created.json()["id"])

        db_session.expire_all()
        vector = await db_session.scalar(select(Post.search_vector).where(Post.id == post_id))
        assert vector is not None
        assert "alphatrigg" in str(vector)
        assert "betatrigg" in str(vector)

        async with make_client(app) as anonymous:
            draft_search = await anonymous.get(
                "/search",
                params={"q": "alphatrigger"},
            )
        assert draft_search.json()["items"] == []

        published = await publish_post(client, str(post_id))
        assert published.status_code == 200

        async with make_client(app) as anonymous:
            published_search = await anonymous.get(
                "/search",
                params={"q": "alphatrigger"},
            )
        assert [item["id"] for item in published_search.json()["items"]] == [str(post_id)]

        db_session.expire_all()
        vector_after_publish = await db_session.scalar(
            select(Post.search_vector).where(Post.id == post_id)
        )
        assert vector_after_publish is not None
        assert "alphatrigg" in str(vector_after_publish)

        title_edit = await client.patch(
            f"/posts/{post_id}",
            json={"title": "GammaTrigger title"},
        )
        assert title_edit.status_code == 200

        db_session.expire_all()
        vector_after_title = await db_session.scalar(
            select(Post.search_vector).where(Post.id == post_id)
        )
        assert vector_after_title is not None
        assert "gammatrigg" in str(vector_after_title)
        assert "alphatrigg" not in str(vector_after_title)
        assert "betatrigg" in str(vector_after_title)

        async with make_client(app) as anonymous:
            old_title = await anonymous.get(
                "/search",
                params={"q": "alphatrigger"},
            )
            old_body = await anonymous.get(
                "/search",
                params={"q": "betatrigger"},
            )
        assert old_title.json()["items"] == []
        assert [item["id"] for item in old_body.json()["items"]] == [str(post_id)]

        body_edit = await client.patch(
            f"/posts/{post_id}",
            json={"body_md": "DeltaTrigger body"},
        )
        assert body_edit.status_code == 200

        db_session.expire_all()
        vector_after_body = await db_session.scalar(
            select(Post.search_vector).where(Post.id == post_id)
        )
        assert vector_after_body is not None
        assert "deltatrigg" in str(vector_after_body)
        assert "betatrigg" not in str(vector_after_body)

        async with make_client(app) as anonymous:
            old_body = await anonymous.get(
                "/search",
                params={"q": "betatrigger"},
            )
            new_body = await anonymous.get(
                "/search",
                params={"q": "deltatrigger"},
            )
        assert old_body.json()["items"] == []
        assert [item["id"] for item in new_body.json()["items"]] == [str(post_id)]


async def test_excerpt_is_first_200_characters_without_body(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    cookies = auth_cookies(author)
    body_md = "x" * 250
    async with make_client(app, cookies) as client:
        published = await _published_post(
            client,
            title="Excerpt Needle",
            body_md=body_md,
        )

    async with make_client(app) as anonymous:
        response = await anonymous.get("/search", params={"q": "needle"})

    item = response.json()["items"][0]
    assert item["id"] == published["id"]
    assert item["excerpt"] == body_md[:200]
    assert "body_md" not in item


async def test_pagination_has_no_duplicates_or_gaps_across_inserts(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    cookies = auth_cookies(author)
    original_ids: set[str] = set()

    async with make_client(app, cookies) as client:
        for index in range(5):
            post = await _published_post(
                client,
                title=f"Pagination Needle {index}",
                body_md=f"Pagination body {index}",
            )
            original_ids.add(post["id"])

    async with make_client(app) as anonymous:
        first = await anonymous.get("/search", params={"q": "needle", "limit": 2})
        assert first.status_code == 200
        collected = list(first.json()["items"])
        cursor = first.json()["next_cursor"]
        assert cursor is not None

        async with make_client(app, cookies) as author_client:
            inserted = await _published_post(
                author_client,
                title="Pagination Needle Inserted",
                body_md="Pagination inserted body",
            )

        while cursor is not None:
            page = await anonymous.get(
                "/search",
                params={"q": "needle", "limit": 2, "cursor": cursor},
            )
            assert page.status_code == 200
            collected.extend(page.json()["items"])
            cursor = page.json()["next_cursor"]

    ids = [item["id"] for item in collected]
    assert len(ids) == len(set(ids))
    assert set(ids) == original_ids
    assert inserted["id"] not in ids


async def test_invalid_and_tampered_cursors_return_400(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    cookies = auth_cookies(author)
    async with make_client(app, cookies) as client:
        await _published_post(client, title="Cursor Needle")
        await _published_post(client, title="Cursor Needle Two")

    async with make_client(app) as anonymous:
        first = await anonymous.get("/search", params={"q": "cursor", "limit": 1})
        cursor = first.json()["next_cursor"]
        assert cursor is not None

        invalid_payload = _cursor_with_payload(
            {
                "rank": "high",
                "published_at": "2026-10-26T00:00:00+00:00",
                "id": "not-a-uuid",
            }
        )
        cases = [
            "not-a-cursor",
            invalid_payload,
            cursor + "AAAA",
            cursor + "!!!!",
            cursor[:-4],
        ]
        for invalid in cases:
            response = await anonymous.get(
                "/search",
                params={"q": "cursor", "cursor": invalid},
            )
            assert response.status_code == 400, invalid


async def test_anonymous_and_authenticated_callers_get_identical_results(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    cookies = auth_cookies(author)
    reader = await create_user(db_session, Role.reader)
    async with make_client(app, cookies) as client:
        await _published_post(
            client,
            title="Identity Needle",
            body_md="Same result for every caller",
        )

    async with make_client(app) as anonymous:
        anonymous_response = await anonymous.get("/search", params={"q": "needle"})

    async with make_client(app, auth_cookies(reader)) as reader_client:
        authenticated_response = await reader_client.get(
            "/search",
            params={"q": "needle"},
        )

    assert anonymous_response.status_code == 200
    assert authenticated_response.status_code == 200
    assert anonymous_response.json() == authenticated_response.json()


async def test_gin_index_is_used_for_typical_query(
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    await db_session.execute(
        text(
            """
            INSERT INTO posts (
                id, author_id, slug, title, body_md, status, published_at,
                created_at, updated_at
            )
            SELECT
                gen_random_uuid(),
                :author_id,
                'search-seed-' || i,
                CASE WHEN i = 1 THEN 'Needle heading' ELSE 'Seed post ' || i END,
                CASE
                    WHEN i <= 3 THEN 'needle ' || repeat('padding ', 40)
                    ELSE 'filler' || i || ' ' || repeat('padding ', 40)
                END,
                'published'::post_status,
                now() - (i || ' seconds')::interval,
                now(),
                now()
            FROM generate_series(1, 10000) AS i
            """
        ),
        {"author_id": author.id},
    )
    await db_session.commit()
    await db_session.execute(text("ANALYZE posts"))
    await db_session.commit()

    result = await db_session.execute(
        text(
            """
            EXPLAIN (FORMAT TEXT)
            SELECT
                id,
                ts_rank(
                    search_vector,
                    websearch_to_tsquery('english', :query)
                ) AS rank
            FROM posts
            WHERE status = 'published'
              AND published_at IS NOT NULL
              AND search_vector @@ websearch_to_tsquery('english', :query)
            ORDER BY rank DESC, published_at DESC, id DESC
            LIMIT 21
            """
        ),
        {"query": "needle"},
    )
    plan = "\n".join(result.scalars().all())
    assert "ix_posts_search_vector" in plan, plan
