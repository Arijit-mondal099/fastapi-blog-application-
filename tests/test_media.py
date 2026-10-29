"""Media upload auth, confirmation, and cover attachment integration tests."""

import hashlib
import hmac
import logging
import time
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.integrations.imagekit import ImageKitClient
from app.models.media import Media, MediaStatus
from app.models.post import Post
from app.models.user import Role
from tests.helpers import make_client
from tests.posts_helpers import auth_cookies, create_post, create_user, publish_post


@pytest.fixture(autouse=True)
def forbid_imagekit_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail if a media flow reaches the real ImageKit client."""

    async def fail(_self: ImageKitClient, _url: str) -> None:
        raise AssertionError("ImageKit network call in media test")

    monkeypatch.setattr(ImageKitClient, "_get", fail)


def _media_payload(
    *,
    imagekit_file_id: str | None = None,
    mime_type: str = "image/jpeg",
    size_bytes: int = 1234,
) -> dict[str, object]:
    return {
        "imagekit_file_id": imagekit_file_id or f"file-{uuid4().hex}",
        "url": "https://ik.imagekit.io/test/media.jpg",
        "mime_type": mime_type,
        "size_bytes": size_bytes,
    }


async def _confirm_media(client, **overrides: object) -> dict:
    response = await client.post("/media", json=_media_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


async def test_upload_auth_access_matrix(app: FastAPI, db_session: AsyncSession) -> None:
    author = await create_user(db_session, Role.author)
    admin = await create_user(db_session, Role.admin)
    reader = await create_user(db_session, Role.reader)

    async with make_client(app) as anonymous:
        assert (await anonymous.get("/media/upload-auth")).status_code == 401

    for user, expected in [
        (reader, 403),
        (author, 200),
        (admin, 200),
    ]:
        async with make_client(app, auth_cookies(user)) as client:
            assert (await client.get("/media/upload-auth")).status_code == expected


async def test_upload_auth_returns_signed_future_expiry(
    app: FastAPI,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    author = await create_user(db_session, Role.author)
    settings = get_settings()
    private_key = settings.imagekit_private_key.get_secret_value()
    caplog.set_level(logging.DEBUG)

    before = int(time.time())
    async with make_client(app, auth_cookies(author)) as client:
        response = await client.get("/media/upload-auth")
    after = int(time.time())

    assert response.status_code == 200
    data = response.json()
    assert set(data) == {
        "token",
        "expire",
        "signature",
        "public_key",
        "folder",
        "file_name",
    }
    assert len(data["token"]) == 32
    int(data["token"], 16)
    assert data["public_key"] == settings.imagekit_public_key
    assert data["folder"] == f"/media/{author.id}"
    assert data["file_name"] == data["token"]
    assert before + 300 <= data["expire"] <= after + 300

    expected_signature = hmac.new(
        private_key.encode("utf-8"),
        f"{data['token']}{data['expire']}".encode(),
        hashlib.sha1,
    ).hexdigest()
    assert data["signature"] == expected_signature
    assert private_key not in response.text
    assert private_key not in caplog.text


@pytest.mark.parametrize("parameter", ["expires_in", "expire"])
async def test_upload_auth_clamps_requested_expiry(
    app: FastAPI,
    db_session: AsyncSession,
    parameter: str,
) -> None:
    author = await create_user(db_session, Role.author)

    before = int(time.time())
    async with make_client(app, auth_cookies(author)) as client:
        response = await client.get("/media/upload-auth", params={parameter: 999999})
    after = int(time.time())

    assert response.status_code == 200
    expire = response.json()["expire"]
    assert before + 3600 <= expire <= after + 3600


async def test_private_key_is_never_returned_or_logged(
    app: FastAPI,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    author = await create_user(db_session, Role.author)
    private_key = get_settings().imagekit_private_key.get_secret_value()
    caplog.set_level(logging.DEBUG)

    async with make_client(app, auth_cookies(author)) as client:
        upload_auth = await client.get("/media/upload-auth")
        media_response = await client.post("/media", json=_media_payload())
        assert media_response.status_code == 201
        media = media_response.json()
        post = (await create_post(client, title="Cover Post")).json()
        patched = await client.patch(
            f"/posts/{post['id']}",
            json={"cover_media_id": media["id"]},
        )

    for response in (upload_auth, media_response, patched):
        assert private_key not in response.text
    assert private_key not in caplog.text


async def test_confirm_media_creates_pending_row_owned_by_caller(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    payload = _media_payload()

    async with make_client(app, auth_cookies(author)) as client:
        response = await client.post("/media", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["owner_id"] == str(author.id)
        assert data["status"] == "pending"
        assert data["imagekit_file_id"] == payload["imagekit_file_id"]

        duplicate = await client.post("/media", json=payload)
        assert duplicate.status_code == 409

    row = await db_session.scalar(select(Media).where(Media.id == UUID(data["id"])))
    assert row is not None
    assert row.owner_id == author.id
    assert row.status is MediaStatus.pending
    assert row.mime_type == "image/jpeg"
    assert row.size_bytes == 1234


async def test_confirm_media_requires_author_or_admin(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    reader = await create_user(db_session, Role.reader)

    async with make_client(app) as anonymous:
        assert (await anonymous.post("/media", json=_media_payload())).status_code == 401

    async with make_client(app, auth_cookies(reader)) as client:
        assert (await client.post("/media", json=_media_payload())).status_code == 403


@pytest.mark.parametrize(
    ("size_bytes", "mime_type"),
    [
        (0, "image/jpeg"),
        (-1, "image/jpeg"),
        (100, ""),
        (100, "   "),
    ],
)
async def test_confirm_media_rejects_invalid_metadata(
    app: FastAPI,
    db_session: AsyncSession,
    size_bytes: int,
    mime_type: str,
) -> None:
    author = await create_user(db_session, Role.author)
    payload = _media_payload(mime_type=mime_type, size_bytes=size_bytes)

    async with make_client(app, auth_cookies(author)) as client:
        response = await client.post("/media", json=payload)

    assert response.status_code == 422
    row = await db_session.scalar(
        select(Media).where(Media.imagekit_file_id == payload["imagekit_file_id"])
    )
    assert row is None


async def test_post_owner_attaches_keeps_and_clears_cover(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)

    async with make_client(app, auth_cookies(author)) as client:
        post = (await create_post(client, title="Cover Post")).json()
        media = await _confirm_media(client)

        attached = await client.patch(
            f"/posts/{post['id']}",
            json={"cover_media_id": media["id"]},
        )
        assert attached.status_code == 200
        assert attached.json()["cover_media_id"] == media["id"]

        title_only = await client.patch(
            f"/posts/{post['id']}",
            json={"title": "New Title"},
        )
        assert title_only.status_code == 200
        assert title_only.json()["cover_media_id"] == media["id"]

        cleared = await client.patch(
            f"/posts/{post['id']}",
            json={"cover_media_id": None},
        )
        assert cleared.status_code == 200
        assert cleared.json()["cover_media_id"] is None

    cover = await db_session.scalar(select(Post.cover_media_id).where(Post.id == UUID(post["id"])))
    assert cover is None


async def test_other_author_gets_404_for_unowned_media(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author_a = await create_user(db_session, Role.author, name="Author A")
    author_b = await create_user(db_session, Role.author, name="Author B")

    async with make_client(app, auth_cookies(author_a)) as client_a:
        post_a = (await create_post(client_a, title="Post A")).json()
        media_a = await _confirm_media(client_a)

    async with make_client(app, auth_cookies(author_b)) as client_b:
        post_b = (await create_post(client_b, title="Post B")).json()
        media_b = await _confirm_media(client_b)

        unowned_media = await client_b.patch(
            f"/posts/{post_b['id']}",
            json={"cover_media_id": media_a["id"]},
        )
        assert unowned_media.status_code == 404
        assert unowned_media.json()["detail"] == "Media not found"

        hidden_post = await client_b.patch(
            f"/posts/{post_a['id']}",
            json={"cover_media_id": media_b["id"]},
        )
        assert hidden_post.status_code == 404

    async with make_client(app, auth_cookies(author_a)) as client_a:
        assert (await publish_post(client_a, post_a["id"])).status_code == 200

    async with make_client(app, auth_cookies(author_b)) as client_b:
        visible_post = await client_b.patch(
            f"/posts/{post_a['id']}",
            json={"cover_media_id": media_b["id"]},
        )
        assert visible_post.status_code == 403

    cover = await db_session.scalar(
        select(Post.cover_media_id).where(Post.id == UUID(post_b["id"]))
    )
    assert cover is None


async def test_admin_can_attach_any_media(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    admin = await create_user(db_session, Role.admin)

    async with make_client(app, auth_cookies(author)) as client:
        post = (await create_post(client, title="Admin Cover Post")).json()
        media = await _confirm_media(client)

    async with make_client(app, auth_cookies(admin)) as client:
        response = await client.patch(
            f"/posts/{post['id']}",
            json={"cover_media_id": media["id"]},
        )

    assert response.status_code == 200
    assert response.json()["cover_media_id"] == media["id"]


async def test_hard_deleting_media_nulls_post_cover(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)

    async with make_client(app, auth_cookies(author)) as client:
        post = (await create_post(client, title="Deleted Cover Post")).json()
        media = await _confirm_media(client)
        attached = await client.patch(
            f"/posts/{post['id']}",
            json={"cover_media_id": media["id"]},
        )
        assert attached.status_code == 200

    await db_session.execute(delete(Media).where(Media.id == UUID(media["id"])))
    await db_session.commit()

    cover = await db_session.scalar(select(Post.cover_media_id).where(Post.id == UUID(post["id"])))
    assert cover is None
    assert await db_session.scalar(select(Post.id).where(Post.id == UUID(post["id"]))) is not None


async def test_media_flow_does_not_call_imagekit(
    app: FastAPI,
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)

    async with make_client(app, auth_cookies(author)) as client:
        upload_auth = await client.get("/media/upload-auth")
        media = await _confirm_media(client)

    assert upload_auth.status_code == 200
    assert media["status"] == "pending"
