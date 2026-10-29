"""Process-level worker tests against real Redis and Postgres containers."""

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from arq.connections import RedisSettings, create_pool
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.worker import WorkerSettings
from app.models.media import Media, MediaStatus
from app.models.refresh_token import RefreshToken
from app.models.user import Role
from tests.posts_helpers import create_user

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_WORKER = "app.jobs.worker.WorkerSettings"
FAKE_IMAGEKIT_WORKER = "tests.job_worker_settings.WorkerSettings"


def _worker_env(postgres_url: str, redis_url: str, **extra: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "test",
            "DATABASE_URL": postgres_url,
            "REDIS_URL": redis_url,
            "JWT_SECRET": "test-jwt-secret-" + "x" * 32,
            "GOOGLE_CLIENT_ID": "test-client-id",
            "GOOGLE_CLIENT_SECRET": "test-client-secret",
            "PYTHONUNBUFFERED": "1",
        }
    )
    env.update(extra)
    return env


async def _start_worker(
    env: dict[str, str],
    *,
    settings_module: str = PRODUCTION_WORKER,
    burst: bool = False,
) -> asyncio.subprocess.Process:
    command = [sys.executable, "-m", "arq", settings_module]
    if burst:
        command.append("--burst")
    return await asyncio.create_subprocess_exec(
        *command,
        cwd=str(ROOT),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )


async def _wait_for(predicate, timeout: float = 30.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await predicate():
            return True
        await asyncio.sleep(0.2)
    return False


def test_worker_settings_register_jobs_and_cron_schedules() -> None:
    job_names = {job.name for job in WorkerSettings.functions}
    cron_jobs = {job.name: job for job in WorkerSettings.cron_jobs}

    assert job_names == {
        "delete_remote_media",
        "verify_media",
        "sweep_orphans",
        "purge_expired_refresh_tokens",
    }
    assert set(cron_jobs) == {"sweep_orphans", "purge_expired_refresh_tokens"}
    assert cron_jobs["sweep_orphans"].minute == 0
    assert cron_jobs["sweep_orphans"].hour is None
    assert cron_jobs["purge_expired_refresh_tokens"].hour == 3
    assert cron_jobs["purge_expired_refresh_tokens"].minute == 0


async def test_worker_consumes_job_end_to_end(
    postgres_url: str,
    redis_url: str,
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, Role.reader)
    expired = RefreshToken(
        user_id=user.id,
        family_id=uuid4(),
        token_hash=f"expired-{uuid4().hex}",
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    db_session.add(expired)
    await db_session.commit()
    expired_id = expired.id

    redis = await create_pool(RedisSettings.from_dsn(redis_url))
    await redis.enqueue_job(
        "purge_expired_refresh_tokens",
        request_id="req-worker-e2e",
    )
    process = await _start_worker(_worker_env(postgres_url, redis_url), burst=True)
    output, _ = await asyncio.wait_for(process.communicate(), timeout=60)
    await redis.aclose()
    text = output.decode("utf-8", errors="replace")

    assert process.returncode == 0, text
    assert "ARQ worker started" in text
    assert "delete_remote_media" in text
    assert "purge_expired_refresh_tokens" in text
    assert '"request_id": "req-worker-e2e"' in text

    db_session.expire_all()
    remaining = await db_session.scalar(
        select(RefreshToken.id).where(RefreshToken.id == expired_id)
    )
    assert remaining is None


async def test_killed_worker_restart_completes_job_once(
    postgres_url: str,
    redis_url: str,
    db_session: AsyncSession,
    tmp_path: Path,
) -> None:
    user = await create_user(db_session, Role.author)
    media = Media(
        owner_id=user.id,
        imagekit_file_id=f"file-{uuid4().hex}",
        url="https://ik.imagekit.io/test/media.jpg",
        mime_type="image/jpeg",
        size_bytes=1234,
        status=MediaStatus.pending_delete,
    )
    db_session.add(media)
    await db_session.commit()
    await db_session.refresh(media)

    start_marker = tmp_path / "started"
    imagekit_log = tmp_path / "imagekit.log"
    redis = await create_pool(RedisSettings.from_dsn(redis_url))
    first_env = _worker_env(
        postgres_url,
        redis_url,
        TEST_IMAGEKIT_DELAY="30",
        TEST_IMAGEKIT_START=str(start_marker),
        TEST_IMAGEKIT_LOG=str(imagekit_log),
    )
    first = await _start_worker(first_env, settings_module=FAKE_IMAGEKIT_WORKER)
    await redis.enqueue_job(
        "delete_remote_media",
        str(media.id),
        request_id="req-kill",
    )

    started = await _wait_for(lambda: asyncio.to_thread(start_marker.exists), timeout=20)
    if not started:
        output, _ = await asyncio.wait_for(first.communicate(), timeout=20)
        raise AssertionError(
            "worker did not start the job: " + output.decode("utf-8", errors="replace")
        )

    first.kill()
    await asyncio.wait_for(first.wait(), timeout=10)
    await first.communicate()
    db_session.expire_all()
    await db_session.refresh(media)
    assert media.status is MediaStatus.pending_delete

    client = Redis.from_url(redis_url, decode_responses=True)
    in_progress = [key async for key in client.scan_iter(match="arq:in-progress:*")]
    if in_progress:
        await client.delete(*in_progress)
    await client.aclose()

    second_env = _worker_env(
        postgres_url,
        redis_url,
        TEST_IMAGEKIT_DELAY="0",
        TEST_IMAGEKIT_START=str(start_marker),
        TEST_IMAGEKIT_LOG=str(imagekit_log),
    )
    second = await _start_worker(second_env, settings_module=FAKE_IMAGEKIT_WORKER)
    try:
        completed = await _wait_for(
            _media_is_deleted(db_session, media.id),
            timeout=30,
        )
        assert completed, "restarted worker did not finish the job"
    finally:
        second.kill()
        output, _ = await asyncio.wait_for(second.communicate(), timeout=10)
        await redis.aclose()

    assert imagekit_log.read_text(encoding="utf-8").splitlines() == [
        f"delete:{media.imagekit_file_id}"
    ]
    assert '"request_id": "req-kill"' in output.decode("utf-8", errors="replace")


def _media_is_deleted(db_session: AsyncSession, media_id):
    async def check() -> bool:
        db_session.expire_all()
        media = await db_session.get(Media, media_id)
        return media is not None and media.status is MediaStatus.deleted

    return check
