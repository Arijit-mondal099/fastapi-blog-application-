"""Phase 7 service and job tests for media outbox and token maintenance."""

import logging
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from arq import Retry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.integrations.imagekit import ImageKitClient, ImageKitClientError, ImageKitFile
from app.jobs import media_jobs
from app.models.media import Media, MediaStatus
from app.models.post import Post, PostStatus
from app.models.refresh_token import RefreshToken
from app.models.user import Role, User
from app.repositories.media import MediaRepository
from app.repositories.posts import PostRepository
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.repositories.tags import TagRepository
from app.repositories.users import UserRepository
from app.services.auth import AuthService
from app.services.media import MediaService
from app.services.posts import PostService
from app.services.tags import TagService
from tests.helpers import FakeJobQueue, make_client
from tests.posts_helpers import auth_cookies, create_user


class FakeImageKit:
    """Deterministic ImageKit substitute that records delete calls."""

    def __init__(self) -> None:
        self.file_details: ImageKitFile | None = None
        self.get_error: Exception | None = None
        self.delete_errors: list[Exception] = []
        self.deleted: list[str] = []

    async def get_file_details(self, file_id: str) -> ImageKitFile | None:
        if self.get_error is not None:
            raise self.get_error
        return self.file_details

    async def delete_file(self, file_id: str) -> None:
        self.deleted.append(file_id)
        if self.delete_errors:
            raise self.delete_errors.pop(0)


def _media_service(
    session: AsyncSession,
    *,
    imagekit: FakeImageKit | None = None,
    queue: FakeJobQueue | None = None,
) -> MediaService:
    return MediaService(
        media=MediaRepository(session),
        posts=PostRepository(session),
        session=session,
        settings=get_settings(),
        imagekit=cast(ImageKitClient, imagekit or FakeImageKit()),
        queue=queue or FakeJobQueue(),
    )


def _post_service(
    session: AsyncSession,
    *,
    media_service: MediaService,
) -> PostService:
    return PostService(
        posts=PostRepository(session),
        tags=TagService(tags=TagRepository(session)),
        media=media_service,
        session=session,
    )


async def _make_media(
    session: AsyncSession,
    owner: User,
    *,
    status: MediaStatus = MediaStatus.pending,
    created_at: datetime | None = None,
    file_id: str | None = None,
) -> Media:
    timestamp = created_at or datetime.now(UTC)
    media = Media(
        owner_id=owner.id,
        imagekit_file_id=file_id or f"file-{uuid4().hex}",
        url="https://ik.imagekit.io/test/media.jpg",
        mime_type="image/jpeg",
        size_bytes=1234,
        status=status,
        created_at=timestamp,
        updated_at=timestamp,
    )
    session.add(media)
    await session.commit()
    await session.refresh(media)
    return media


async def _make_post(
    session: AsyncSession,
    author: User,
    *,
    cover_media_id: UUID | None = None,
) -> Post:
    post = Post(
        author_id=author.id,
        slug=f"post-{uuid4().hex}",
        title="Post",
        body_md="Body",
        status=PostStatus.draft,
        cover_media_id=cover_media_id,
    )
    session.add(post)
    await session.commit()
    await session.refresh(post)
    return post


async def test_deleting_post_marks_cover_pending_delete_and_enqueues_after_commit(
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    media = await _make_media(db_session, author, status=MediaStatus.verified)
    post = await _make_post(db_session, author, cover_media_id=media.id)
    queue = FakeJobQueue()
    service = _post_service(db_session, media_service=_media_service(db_session, queue=queue))

    await service.delete(actor_id=author.id, actor_role=Role.author, post_id=post.id)

    await db_session.refresh(media)
    assert media.status is MediaStatus.pending_delete
    assert await db_session.scalar(select(Post.id).where(Post.id == post.id)) is None
    assert queue.jobs == [("delete_remote_media", (str(media.id),), None)]


async def test_rollback_leaves_media_unchanged_and_enqueues_nothing(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    author = await create_user(db_session, Role.author)
    media = await _make_media(db_session, author, status=MediaStatus.verified)
    post = await _make_post(db_session, author, cover_media_id=media.id)
    post_id = post.id
    queue = FakeJobQueue()
    service = _post_service(db_session, media_service=_media_service(db_session, queue=queue))

    async def fail_commit() -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(db_session, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="commit failed"):
        await service.delete(actor_id=author.id, actor_role=Role.author, post_id=post_id)
    await db_session.rollback()

    await db_session.refresh(media)
    assert media.status is MediaStatus.verified
    assert queue.jobs == []
    assert await db_session.scalar(select(Post.id).where(Post.id == post_id)) is not None


async def test_failed_enqueue_does_not_fail_post_delete(
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    author = await create_user(db_session, Role.author)
    media = await _make_media(db_session, author, status=MediaStatus.verified)
    post = await _make_post(db_session, author, cover_media_id=media.id)
    queue = FakeJobQueue()
    queue.fail = True
    service = _post_service(db_session, media_service=_media_service(db_session, queue=queue))
    caplog.set_level(logging.INFO)

    await service.delete(
        actor_id=author.id,
        actor_role=Role.author,
        post_id=post.id,
        request_id="req-enqueue-failure",
    )

    await db_session.refresh(media)
    assert media.status is MediaStatus.pending_delete
    assert await db_session.scalar(select(Post.id).where(Post.id == post.id)) is None
    records = [record for record in caplog.records if record.name == "app.services.media"]
    assert any(record.request_id == "req-enqueue-failure" for record in records)


async def test_delete_remote_media_is_idempotent(db_session: AsyncSession) -> None:
    author = await create_user(db_session, Role.author)
    media = await _make_media(db_session, author, status=MediaStatus.pending_delete)
    imagekit = FakeImageKit()
    service = _media_service(db_session, imagekit=imagekit)

    await service.delete_remote_media(media.id)
    await service.delete_remote_media(media.id)

    assert imagekit.deleted == [media.imagekit_file_id]
    await db_session.refresh(media)
    assert media.status is MediaStatus.deleted


async def test_delete_remote_media_retries_then_succeeds(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    author = await create_user(db_session, Role.author)
    media = await _make_media(db_session, author, status=MediaStatus.pending_delete)
    imagekit = FakeImageKit()
    imagekit.delete_errors = [ImageKitClientError("temporary")]
    service = _media_service(db_session, imagekit=imagekit)
    monkeypatch.setattr(media_jobs, "_build_media_service", lambda session, ctx: service)

    with pytest.raises(Retry):
        await media_jobs.delete_remote_media({"job_try": 1}, str(media.id))
    await media_jobs.delete_remote_media({"job_try": 2}, str(media.id))

    await db_session.refresh(media)
    assert media.status is MediaStatus.deleted
    assert len(imagekit.deleted) == 2


async def test_delete_remote_media_fails_permanently_after_five_attempts(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    author = await create_user(db_session, Role.author)
    media = await _make_media(db_session, author, status=MediaStatus.pending_delete)
    imagekit = FakeImageKit()
    imagekit.delete_errors = [ImageKitClientError("temporary") for _ in range(5)]
    service = _media_service(db_session, imagekit=imagekit)
    monkeypatch.setattr(media_jobs, "_build_media_service", lambda session, ctx: service)

    for attempt in range(1, 5):
        with pytest.raises(Retry):
            await media_jobs.delete_remote_media({"job_try": attempt}, str(media.id))
    with pytest.raises(ImageKitClientError):
        await media_jobs.delete_remote_media({"job_try": 5}, str(media.id))

    await db_session.refresh(media)
    assert media.status is MediaStatus.pending_delete
    assert len(imagekit.deleted) == 5


async def test_verify_media_marks_valid_file_verified(db_session: AsyncSession) -> None:
    author = await create_user(db_session, Role.author)
    media = await _make_media(db_session, author)
    imagekit = FakeImageKit()
    imagekit.file_details = ImageKitFile(
        file_id=media.imagekit_file_id,
        url=media.url,
        mime_type="image/png",
        size_bytes=2048,
        file_path=None,
    )
    service = _media_service(db_session, imagekit=imagekit)

    await service.verify_media(media.id)
    await service.verify_media(media.id)

    await db_session.refresh(media)
    assert media.status is MediaStatus.verified


@pytest.mark.parametrize(
    "details",
    [
        ImageKitFile("file", "https://example.test/a", "text/plain", 100, None),
        ImageKitFile("file", "https://example.test/a", "image/jpeg", 10 * 1024 * 1024 + 1, None),
        ImageKitFile("file", "https://example.test/a", "image/jpeg", None, None),
    ],
)
async def test_verify_media_rejects_invalid_metadata_and_schedules_deletion(
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
    details: ImageKitFile,
) -> None:
    author = await create_user(db_session, Role.author)
    media = await _make_media(db_session, author)
    imagekit = FakeImageKit()
    imagekit.file_details = details
    queue = FakeJobQueue()
    service = _media_service(db_session, imagekit=imagekit, queue=queue)
    caplog.set_level(logging.INFO)

    await service.verify_media(media.id)

    await db_session.refresh(media)
    assert media.status is MediaStatus.pending_delete
    assert queue.jobs == [("delete_remote_media", (str(media.id),), None)]
    assert any("media verification failed" in record.message for record in caplog.records)


async def test_sweep_reenqueues_pending_delete_and_retires_old_orphans(
    db_session: AsyncSession,
) -> None:
    author = await create_user(db_session, Role.author)
    old = datetime.now(UTC) - timedelta(hours=25)
    pending_delete = await _make_media(db_session, author, status=MediaStatus.pending_delete)
    old_orphan = await _make_media(db_session, author, created_at=old)
    covered_old = await _make_media(db_session, author, created_at=old)
    await _make_post(db_session, author, cover_media_id=covered_old.id)
    recent = await _make_media(
        db_session,
        author,
        created_at=datetime.now(UTC) - timedelta(hours=1),
    )
    queue = FakeJobQueue()
    service = _media_service(db_session, queue=queue)

    await service.sweep_orphans(now=datetime.now(UTC))

    enqueued = {args[0] for function, args, _ in queue.jobs if function == "delete_remote_media"}
    assert enqueued == {str(pending_delete.id), str(old_orphan.id)}

    for media in (pending_delete, old_orphan, covered_old, recent):
        await db_session.refresh(media)
    assert pending_delete.status is MediaStatus.pending_delete
    assert old_orphan.status is MediaStatus.pending_delete
    assert covered_old.status is MediaStatus.pending
    assert recent.status is MediaStatus.pending


async def test_sweep_is_idempotent(db_session: AsyncSession) -> None:
    author = await create_user(db_session, Role.author)
    media = await _make_media(db_session, author, status=MediaStatus.pending_delete)
    queue = FakeJobQueue()
    service = _media_service(db_session, queue=queue)

    await service.sweep_orphans(now=datetime.now(UTC))
    await service.sweep_orphans(now=datetime.now(UTC))

    assert [args for function, args, _ in queue.jobs if function == "delete_remote_media"] == [
        (str(media.id),),
        (str(media.id),),
    ]


async def test_purge_expired_refresh_tokens_removes_only_expired_or_old_revoked(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, Role.reader)
    now = datetime.now(UTC)
    rows = {
        "expired": RefreshToken(
            user_id=user.id,
            family_id=uuid4(),
            token_hash=f"expired-{uuid4().hex}",
            expires_at=now - timedelta(days=1),
        ),
        "old_revoked": RefreshToken(
            user_id=user.id,
            family_id=uuid4(),
            token_hash=f"old-revoked-{uuid4().hex}",
            expires_at=now + timedelta(days=10),
            revoked_at=now - timedelta(days=31),
        ),
        "recent_revoked": RefreshToken(
            user_id=user.id,
            family_id=uuid4(),
            token_hash=f"recent-revoked-{uuid4().hex}",
            expires_at=now + timedelta(days=10),
            revoked_at=now - timedelta(days=1),
        ),
        "valid": RefreshToken(
            user_id=user.id,
            family_id=uuid4(),
            token_hash=f"valid-{uuid4().hex}",
            expires_at=now + timedelta(days=10),
        ),
    }
    db_session.add_all(rows.values())
    await db_session.commit()
    service = AuthService(
        users=UserRepository(db_session),
        refresh_tokens=RefreshTokenRepository(db_session),
        session=db_session,
        settings=get_settings(),
        clock=lambda: now,
    )

    deleted = await service.purge_expired_refresh_tokens()

    remaining = set(
        (
            await db_session.scalars(
                select(RefreshToken.token_hash).where(
                    RefreshToken.token_hash.in_([row.token_hash for row in rows.values()])
                )
            )
        ).all()
    )
    assert deleted == 2
    assert remaining == {rows["recent_revoked"].token_hash, rows["valid"].token_hash}


async def test_job_logs_carry_the_enqueuing_request_id(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    author = await create_user(db_session, Role.author)
    media = await _make_media(db_session, author, status=MediaStatus.pending_delete)
    service = _media_service(db_session, imagekit=FakeImageKit())
    monkeypatch.setattr(media_jobs, "_build_media_service", lambda session, ctx: service)
    caplog.set_level(logging.INFO)

    await media_jobs.delete_remote_media(
        {"job_try": 1},
        str(media.id),
        request_id="req-job-123",
    )

    job_records = [
        record
        for record in caplog.records
        if record.name.startswith(("app.jobs.", "app.services.media"))
    ]
    assert job_records
    assert {record.request_id for record in job_records} == {"req-job-123"}


async def test_http_request_id_is_propagated_to_enqueued_jobs(
    app,
    db_session: AsyncSession,
    fake_job_queue: FakeJobQueue,
) -> None:
    author = await create_user(db_session, Role.author)

    async with make_client(app, auth_cookies(author)) as client:
        response = await client.post(
            "/media",
            json={
                "imagekit_file_id": f"file-{uuid4().hex}",
                "url": "https://ik.imagekit.io/test/media.jpg",
                "mime_type": "image/jpeg",
                "size_bytes": 1234,
            },
            headers={"X-Request-ID": "req-http-456"},
        )

    assert response.status_code == 201
    assert response.headers["X-Request-ID"] == "req-http-456"
    assert fake_job_queue.jobs[0][0] == "verify_media"
    assert fake_job_queue.jobs[0][2] == "req-http-456"
